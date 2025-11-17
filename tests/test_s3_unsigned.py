import boto3
from botocore import UNSIGNED
from botocore.config import Config
import geopandas as gpd
from io import BytesIO


# Unsigned client (no credentials needed for public bucket)
s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))

bucket = "catasto-2025"
key = "ITALIA/ABRUZZO/AQ/A018_ACCIANO/A018_ACCIANO_map.gpkg"

# Get the object into memory
obj = s3.get_object(Bucket=bucket, Key=key)
body = obj["Body"]

# Save to an in-memory file-like object
file_like = BytesIO(body.read())

# Read directly with geopandas
gdf = gpd.read_file(file_like, layer=0)

print(gdf.head().T)
