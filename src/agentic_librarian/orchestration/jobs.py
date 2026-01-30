from agentic_librarian.etl.enhance import enhanced_book_features
from dagster import define_asset_job

enhance_job = define_asset_job(name="enhance_job", selection=[enhanced_book_features])
