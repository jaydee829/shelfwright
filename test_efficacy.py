
import os
import mlflow
import src.agentic_librarian.scouts.metadata_scout as md_scout

# Set environment variables for testing (mock/live depending on env)
# os.environ["USE_SEARCH_GROUNDING"] = "1" 

books = [
    {"title": "The Way of Kings", "author": "Brandon Sanderson"}, # Popular, complex
    {"title": "Project Hail Mary", "author": "Andy Weir"}, # Very popular audiobook
    {"title": "The 7 1/2 Deaths of Evelyn Hardcastle", "author": "Stuart Turton"}, # Unique titles
    {"title": "Dungeons and Drama", "author": "Kristy Boyce"}, # Newer/less obscure
    {"title": "The Martian", "author": "Andy Weir"} # Iconic
]

def run_efficacy_test():
    scout = md_scout.MultiSourceScout()
    
    # Mock MLFlow for terminal output if needed, but the code already does it.
    # If MLFlow isn't running, it might fail or log locally.
    
    print("Starting Efficacy Test...")
    for book in books:
        print(f"Scouting: {book['title']} by {book['author']}")
        result = scout.scout_metadata(book['title'], book['author'], format="Audiobook")
        print(f"Result: {result['audio_minutes']} minutes (Source Priority: {result['source_priority']})")
        print("-" * 20)

if __name__ == "__main__":
    run_efficacy_test()
