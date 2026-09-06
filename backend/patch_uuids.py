import os
import re
import sys

uuid_field = '    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)\n'
import_uuid = 'import uuid\n'

for root, dirs, files in os.walk('c:/Users/USER/IPT-system/backend/apps'):
    if 'models.py' in files:
        path = os.path.join(root, 'models.py')
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        new_lines = []
        needs_import = False
        
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Check if this line is a target class definition
            if re.match(r'^class \w+\(.*(?:models\.Model|AbstractUser).*\):', line):
                needs_import = True
                new_lines.append(line)
                
                i += 1
                if i < len(lines):
                    next_line = lines[i]
                    if next_line.strip().startswith('"""'):
                        new_lines.append(next_line)
                        if next_line.strip() == '"""' or not next_line.strip().endswith('"""'):
                            i += 1
                            while i < len(lines) and not lines[i].strip().endswith('"""'):
                                new_lines.append(lines[i])
                                i += 1
                            if i < len(lines):
                                new_lines.append(lines[i])
                        new_lines.append(uuid_field.rstrip('\n'))
                    else:
                        new_lines.append(uuid_field.rstrip('\n'))
                        new_lines.append(next_line)
            else:
                new_lines.append(line)
            i += 1

        if needs_import and 'import uuid' not in content:
            new_lines.insert(0, import_uuid.rstrip('\n'))
            
        if needs_import:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(new_lines))
            print(f'Patched: {path}')

