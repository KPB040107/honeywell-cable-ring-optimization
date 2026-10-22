with open('src/resplan_router/phase1_ingestion/resplan_adapter.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped in ['"garden",', '"pool",', '"parking",', '"land",']:
        continue
    new_lines.append(line)

with open('src/resplan_router/phase1_ingestion/resplan_adapter.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Updated resplan_adapter.py: {len(lines)} -> {len(new_lines)} lines")
