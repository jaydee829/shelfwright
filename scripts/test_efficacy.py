import agentic_librarian.scouts.metadata_scout as md_scout


def run_efficacy_test():
    # Use the new ScoutManager instead of the legacy MultiSourceScout
    manager = md_scout.ScoutManager()
    manager.register_scout(md_scout.HardcoverScout(), priority=1)
    manager.register_scout(md_scout.GoogleBooksScout(), priority=2)
    manager.register_scout(md_scout.AudiobookScout(), priority=3)
    manager.register_scout(md_scout.DirectKnowledgeScout(), priority=4)

    print("Starting Efficacy Test...")
    books = [
        {"title": "The Way of Kings", "author": "Brandon Sanderson"},
        {"title": "Project Hail Mary", "author": "Andy Weir"},
        {"title": "The 7 1/2 Deaths of Evelyn Hardcastle", "author": "Stuart Turton"},
        {"title": "Dungeons and Drama", "author": "Kristy Boyce"},
        {"title": "The Martian", "author": "Andy Weir"},
    ]

    for book in books:
        print(f"Scouting: {book['title']} by {book['author']}")
        result = manager.enrich(book["title"], book["author"], format="Audiobook")
        # Format results for output
        audio_min = result.get("audio_minutes", "N/A")
        priority = result.get("source_priority", [])
        print(f"Result: {audio_min} minutes (Source Priority: {priority})")
        print("-" * 20)


if __name__ == "__main__":
    run_efficacy_test()
