"""Seed script for Travel Concierge Agent Firestore backend."""

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-e866ea80fe72"

DESTINATIONS = [
    {
        "id": "kyoto-japan",
        "name": "Kyoto",
        "country": "Japan",
        "budget_tier": "moderate",
        "tags": ["culture", "temples", "food", "walkable"],
        "description": "Ancient Japanese capital known for historic Buddhist temples, gardens, and imperial palaces.",
        "highlights": ["Fushimi Inari-taisha", "Kinkaku-ji (Golden Pavilion)", "Arashiyama Bamboo Grove", "Gion District"],
        "recommended_days": 4,
        "daily_estimated_cost_usd": 140,
    },
    {
        "id": "barcelona-spain",
        "name": "Barcelona",
        "country": "Spain",
        "budget_tier": "moderate",
        "tags": ["architecture", "beach", "tapas", "nightlife"],
        "description": "Vibrant seaside city renowned for Antoni Gaudí architecture, Gothic quarters, and Catalan cuisine.",
        "highlights": ["La Sagrada Família", "Park Güell", "Gothic Quarter", "Barceloneta Beach"],
        "recommended_days": 3,
        "daily_estimated_cost_usd": 150,
    },
    {
        "id": "banff-canada",
        "name": "Banff National Park",
        "country": "Canada",
        "budget_tier": "luxury",
        "tags": ["nature", "hiking", "mountains", "scenic"],
        "description": "Spectacular Canadian Rocky Mountain destination with turquoise glacial lakes and alpine trails.",
        "highlights": ["Lake Louise", "Moraine Lake", "Banff Gondola", "Johnston Canyon"],
        "recommended_days": 5,
        "daily_estimated_cost_usd": 220,
    },
    {
        "id": "chiang-mai-thailand",
        "name": "Chiang Mai",
        "country": "Thailand",
        "budget_tier": "budget",
        "tags": ["budget-friendly", "food", "temples", "night-markets"],
        "description": "Northern Thailand mountain hub famed for night bazaars, ornate lanna temples, and culinary scene.",
        "highlights": ["Wat Phra That Doi Suthep", "Sunday Walking Street", "Elephant Nature Park", "Old City Moat"],
        "recommended_days": 4,
        "daily_estimated_cost_usd": 50,
    },
]

def seed():
    print(f"Connecting to Firestore for project: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection("destinations")
    
    for item in DESTINATIONS:
        doc_id = item["id"]
        doc_ref = collection_ref.document(doc_id)
        data = {k: v for k, v in item.items() if k != "id"}
        doc_ref.set(data)
        print(f"✓ Seeded destination: {item['name']} ({doc_id})")

    print("\nFirestore seeding completed successfully!")

if __name__ == "__main__":
    seed()
