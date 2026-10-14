"""Package the exact retained parser with its desktop runner; no profiles or secrets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root=Path(__file__).resolve().parent.parent
target=root/'public/downloads/takealot-native-collector.zip'
with ZipFile(target,'w',ZIP_DEFLATED) as archive:
    for name in ('collector.py','requirements.txt','README.md'):
        archive.write(root/'tools/native-collector'/name,name)
    archive.write(root/'backend/services/pricepilot_collector.py','pricepilot_collector.py')
print(target.name)
