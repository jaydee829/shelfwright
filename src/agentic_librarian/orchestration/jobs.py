from dagster import AssetSelection, define_asset_job

enhance_job = define_asset_job(
    name="enhance_job",
    selection=AssetSelection.assets("raw_history")
    | AssetSelection.assets("enriched_metadata")
    | AssetSelection.assets("vectorized_tropes"),
)
