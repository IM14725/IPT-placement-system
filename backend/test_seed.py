import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
import json
from django.conf import settings
from django.utils.text import slugify
from apps.locations.models import Region, District

print('Loading locations...')
loc_path = settings.LOCATIONS_DATA_FILE
with open(loc_path, encoding='utf-8') as fh:
    loc_payload = json.load(fh)

print('Seeding regions...')
created_regions = 0
created_districts = 0
for i, item in enumerate(loc_payload['regions']):
    print(f'Creating region {item["name"]}...')
    region, region_created = Region.objects.get_or_create(
        name=item['name'], defaults={'slug': slugify(item['name'])}
    )
    if region_created:
        created_regions += 1
    
    print(f'Creating districts for {item["name"]}...')
    for district_name in item['districts']:
        _, created = District.objects.get_or_create(
            name=district_name, region=region
        )
        if created:
            created_districts += 1
print('Done!')

