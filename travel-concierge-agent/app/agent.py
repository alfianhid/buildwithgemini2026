# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import os
import re
import uuid
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

MODEL = "gemini-3.7-flash"
PROJECT_ID = "qwiklabs-gcp-03-e866ea80fe72"
IMAGE_MODEL = "gemini-3.1-flash-lite-image"
IMAGE_LOCATION = "global"
MEDIA_BUCKET_NAME = "travel-concierge-media-qwiklabs-gcp-03-e866ea80fe72"

# Initialize clients lazily
_db: Optional[firestore.Client] = None
_storage_client: Optional[storage.Client] = None
_genai_client: Optional[genai.Client] = None


def get_db() -> firestore.Client:
    """Lazy initialize Firestore client to avoid premature connection during imports."""
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db


def get_storage_client() -> storage.Client:
    """Lazy initialize Cloud Storage client."""
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=PROJECT_ID)
    return _storage_client


def get_genai_client() -> genai.Client:
    """Lazy initialize GenAI client configured for Vertex AI in global region."""
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=IMAGE_LOCATION,
        )
    return _genai_client


def list_destinations(budget_tier: Optional[str] = None, tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """List curated travel destinations from Firestore, optionally filtered by budget tier or tag.

    Args:
        budget_tier: Optional budget tier filter (e.g. 'budget', 'moderate', 'luxury').
        tag: Optional interest or style tag (e.g. 'culture', 'beach', 'nature', 'food').

    Returns:
        A list of destination dictionaries containing details and highlights.
    """
    db = get_db()
    docs = db.collection("destinations").stream()
    results = []
    for doc in docs:
        item = doc.to_dict()
        item["id"] = doc.id
        if budget_tier and item.get("budget_tier", "").lower() != budget_tier.lower():
            continue
        if tag:
            tags = [t.lower() for t in item.get("tags", [])]
            if tag.lower() not in tags:
                continue
        results.append(item)
    return results


def get_destination_details(destination_id: str) -> Dict[str, Any]:
    """Retrieve full details for a specific destination by its ID.

    Args:
        destination_id: The document ID of the destination (e.g., 'kyoto-japan', 'barcelona-spain').

    Returns:
        The destination record dictionary or an error message if not found.
    """
    db = get_db()
    doc_ref = db.collection("destinations").document(destination_id)
    doc = doc_ref.get()
    if not doc.exists:
        return {"error": f"Destination with ID '{destination_id}' was not found."}
    data = doc.to_dict()
    data["id"] = doc.id
    return data


def save_destination(
    destination_id: str,
    name: str,
    country: str,
    budget_tier: str,
    description: str,
    highlights: List[str],
    tags: List[str],
    recommended_days: int = 3,
    daily_estimated_cost_usd: int = 100,
) -> Dict[str, Any]:
    """Add or update a travel destination in the Firestore collection.

    Args:
        destination_id: Unique slug ID for the destination (e.g. 'rome-italy').
        name: Common name of the city or region (e.g. 'Rome').
        country: Country name (e.g. 'Italy').
        budget_tier: Budget tier ('budget', 'moderate', or 'luxury').
        description: Brief summary of why to visit.
        highlights: Key attractions or points of interest.
        tags: Categorization tags (e.g. ['history', 'food', 'walkable']).
        recommended_days: Recommended trip length in days.
        daily_estimated_cost_usd: Estimated average daily cost in USD per person.

    Returns:
        A confirmation dictionary indicating success.
    """
    db = get_db()
    data = {
        "name": name,
        "country": country,
        "budget_tier": budget_tier.lower(),
        "description": description,
        "highlights": highlights,
        "tags": [t.lower() for t in tags],
        "recommended_days": recommended_days,
        "daily_estimated_cost_usd": daily_estimated_cost_usd,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db.collection("destinations").document(destination_id).set(data)
    return {"status": "success", "destination_id": destination_id, "data": data}


def save_user_itinerary(
    trip_name: str,
    destination: str,
    days: int,
    notes: str,
) -> Dict[str, Any]:
    """Save a planned itinerary or trip notes for the traveler into Firestore.

    Args:
        trip_name: Title for the trip (e.g., 'Spring Kyoto Trip').
        destination: Destination name.
        days: Duration in days.
        notes: Itinerary breakdown, planned activities, or notes.

    Returns:
        A confirmation dictionary with the saved itinerary ID.
    """
    db = get_db()
    trip_id = trip_name.lower().replace(" ", "-")
    data = {
        "trip_name": trip_name,
        "destination": destination,
        "days": days,
        "notes": notes,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    db.collection("itineraries").document(trip_id).set(data)
    return {"status": "success", "itinerary_id": trip_id}


def calculate_trip_budget(
    destination_id: str,
    days: int,
    num_travelers: int = 1,
    travel_style: str = "moderate",
    include_flight_estimate: bool = True,
) -> Dict[str, Any]:
    """Calculate an estimated trip budget breakdown based on destination data and trip parameters.

    Args:
        destination_id: The document ID of the destination (e.g. 'kyoto-japan', 'barcelona-spain').
        days: Duration of the trip in days.
        num_travelers: Number of people traveling (default: 1).
        travel_style: Budget preference ('budget', 'moderate', or 'luxury').
        include_flight_estimate: Whether to add a baseline flight estimate.

    Returns:
        A detailed cost breakdown dictionary with line items and total estimated cost in USD.
    """
    dest = get_destination_details(destination_id)
    base_daily = dest.get("daily_estimated_cost_usd", 120) if "error" not in dest else 120
    dest_name = dest.get("name", destination_id)

    # Style multipliers
    multipliers = {
        "budget": 0.75,
        "moderate": 1.0,
        "luxury": 1.75,
    }
    style_mult = multipliers.get(travel_style.lower(), 1.0)
    adjusted_daily = int(base_daily * style_mult)

    # Breakdown components
    lodging_total = int(adjusted_daily * 0.45 * days * max(1, (num_travelers + 1) // 2))
    food_total = int(adjusted_daily * 0.35 * days * num_travelers)
    activities_total = int(adjusted_daily * 0.20 * days * num_travelers)
    
    flight_estimate = (500 * num_travelers) if include_flight_estimate else 0
    subtotal = lodging_total + food_total + activities_total + flight_estimate
    contingency = int(subtotal * 0.10)
    grand_total = subtotal + contingency

    return {
        "destination": dest_name,
        "days": days,
        "num_travelers": num_travelers,
        "travel_style": travel_style.lower(),
        "breakdown_usd": {
            "lodging": lodging_total,
            "food_and_dining": food_total,
            "activities_and_sightseeing": activities_total,
            "flights_estimate": flight_estimate,
            "contingency_buffer_10pct": contingency,
        },
        "daily_per_person_usd": adjusted_daily,
        "total_estimated_usd": grand_total,
    }


def fetch_flight_price_trends(
    origin: str,
    destination: Optional[str] = None,
    currency: str = "usd",
    limit: int = 5,
) -> Dict[str, Any]:
    """Fetch flight price trends and cheap routes from a departure city using Piratefly API.

    Args:
        origin: 3-letter IATA code or city code of departure (e.g., 'NYC', 'SFO', 'LON', 'TYO').
        destination: Optional 3-letter IATA code of destination (e.g., 'BCN', 'TYO', 'MIA').
        currency: Currency code (default: 'usd').
        limit: Maximum number of flight deals to return (default: 5).

    Returns:
        A dictionary with origin, currency, and a list of flight trend deals.
    """
    url = "https://piratefly.com/api/v1/price-trends"
    params: Dict[str, Any] = {
        "origin": origin.strip().upper(),
        "currency": currency.strip().lower(),
    }
    if destination:
        params["destination"] = destination.strip().upper()

    headers = {}
    api_key = os.environ.get("PIRATEFLY_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=10.0)
        if response.status_code != 200:
            return {
                "error": f"Piratefly API returned status {response.status_code}: {response.text[:200]}"
            }
        data = response.json()
        flight_deals = data.get("data", [])[:limit]
        formatted = []
        for deal in flight_deals:
            formatted.append({
                "origin": deal.get("origin"),
                "destination": deal.get("destination"),
                "depart_date": deal.get("depart_date"),
                "return_date": deal.get("return_date"),
                "price": deal.get("value"),
                "currency": data.get("currency", currency),
                "transfers": deal.get("number_of_changes", 0),
                "airline_gate": deal.get("gate"),
            })
        return {
            "origin": origin.upper(),
            "destination_filter": destination.upper() if destination else "all",
            "deals": formatted,
        }
    except Exception as e:
        return {"error": f"Failed to fetch flight trends: {str(e)}"}


def geocode_address(address: str) -> Dict[str, Any]:
    """Geocode an address or city name into latitude/longitude coordinates using Google Maps Geocoding API.

    Args:
        address: The address, city, landmark, or region name (e.g., 'Kyoto, Japan', 'Sagrada Familia, Barcelona').

    Returns:
        A dictionary containing formatted address, latitude, longitude, and place ID.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY is not configured in the environment."}

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": api_key}
    try:
        response = httpx.get(url, params=params, timeout=10.0)
        data = response.json()
        if data.get("status") != "OK":
            return {
                "error": f"Geocoding failed with status: {data.get('status')}",
                "message": data.get("error_message", ""),
            }
        result = data["results"][0]
        location = result["geometry"]["location"]
        return {
            "formatted_address": result.get("formatted_address"),
            "latitude": location.get("lat"),
            "longitude": location.get("lng"),
            "place_id": result.get("place_id"),
        }
    except Exception as e:
        return {"error": f"Geocoding request failed: {str(e)}"}


def search_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "tourist_attraction",
    radius_meters: float = 3000.0,
    max_results: int = 5,
) -> Dict[str, Any]:
    """Find nearby places of a given type around a coordinate using Google Places API (New).

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        place_type: Type of place to search for (e.g., 'tourist_attraction', 'restaurant', 'museum', 'park', 'cafe').
        radius_meters: Search radius in meters (default: 3000m / 3km).
        max_results: Maximum number of places to return (default: 5).

    Returns:
        A list of nearby places with display name, formatted address, location coordinates, and rating.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY is not configured in the environment."}

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.rating,places.types",
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": max_results,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": radius_meters,
            }
        },
    }

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        if response.status_code != 200:
            return {
                "error": f"Places API returned status {response.status_code}: {response.text[:250]}"
            }
        data = response.json()
        places = data.get("places", [])
        results = []
        for p in places:
            display_name = p.get("displayName", {}).get("text", "")
            address = p.get("formattedAddress", "")
            loc = p.get("location", {})
            rating = p.get("rating")
            results.append({
                "name": display_name,
                "address": address,
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "rating": rating,
            })
        return {
            "search_center": {"latitude": latitude, "longitude": longitude},
            "place_type": place_type,
            "places": results,
        }
    except Exception as e:
        return {"error": f"Places search failed: {str(e)}"}


async def generate_destination_image(
    destination: str,
    prompt: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Generate a scenic travel/destination image using gemini-3.1-flash-lite-image, save it as an artifact, and upload it to the public Cloud Storage bucket.

    Args:
        destination: Name of the destination, attraction, or travel item (e.g. 'Kyoto, Japan', 'Eiffel Tower at sunset', 'Santorini beach').
        prompt: Optional specific visual description or style notes (e.g. 'vibrant cherry blossoms at golden hour, scenic postcard style').
        tool_context: ADK ToolContext used to save the generated image as an artifact for the Playground.

    Returns:
        A dictionary containing the status, destination name, saved artifact status, and public HTTPS URL in Cloud Storage.
    """
    client = get_genai_client()

    full_prompt = f"A high-quality, scenic travel photo of {destination}"
    if prompt:
        full_prompt = f"{full_prompt}. {prompt}"
    else:
        full_prompt = f"{full_prompt}, scenic postcard style, beautiful lighting, vibrant colors"

    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(
                    image_size="1K",
                    aspect_ratio="1:1",
                ),
            ),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if not image_bytes:
            return {"error": "No image data was generated by the model."}

        ext = "png" if "png" in mime_type.lower() else "jpg"
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", destination.lower()).strip("_") or "image"
        unique_id = uuid.uuid4().hex[:8]
        filename = f"{slug}_{unique_id}.{ext}"

        # 1. Save artifact to ADK tool_context (Playground Artifacts panel)
        artifact_saved = False
        if tool_context is not None:
            try:
                artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                await tool_context.save_artifact(filename=filename, artifact=artifact_part)
                artifact_saved = True
            except Exception:
                pass

        # 2. Upload image bytes directly to public GCS bucket
        storage_client = get_storage_client()
        bucket = storage_client.bucket(MEDIA_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{MEDIA_BUCKET_NAME}/{filename}"

        return {
            "status": "success",
            "destination": destination,
            "filename": filename,
            "artifact_saved": artifact_saved,
            "url": public_url,
            "public_url": public_url,
        }
    except Exception as e:
        return {"error": f"Failed to generate and upload image: {str(e)}"}


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: after each turn, send the session to Memory Bank for durable fact extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception:
        pass
    return None


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are the Travel & Itinerary Concierge Agent. "
        "You help travelers explore destinations, learn about local highlights and costs, "
        "and build customized travel itineraries. "
        "You remember the user's travel preferences, budget styles, and facts from previous conversations "
        "and use them to personalize your recommendations. "
        "Use your tools to query curated destinations (`list_destinations`, `get_destination_details`), "
        "add new destinations if requested (`save_destination`), calculate personalized cost estimates "
        "(`calculate_trip_budget`), look up real flight price trends (`fetch_flight_price_trends`), "
        "geocode locations (`geocode_address`), find nearby attractions/restaurants (`search_nearby_places`), "
        "generate visual destination inspiration images (`generate_destination_image`), "
        "and save customized itineraries (`save_user_itinerary`)."
    ),
    workflow_description="Analyze the user request, call any appropriate tools to gather information, and return structured UI cards when presenting destinations, flight deals, places, images, or itineraries.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        list_destinations,
        get_destination_details,
        calculate_trip_budget,
        fetch_flight_price_trends,
        geocode_address,
        search_nearby_places,
        generate_destination_image,
        save_destination,
        save_user_itinerary,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)

