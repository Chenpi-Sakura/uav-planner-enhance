import os

file_path = "schemas.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    "    waypoints: List[Point]",
    "    waypoints: List[Point]\n    date: str = '2022-05-30'\n    time_slot: str = 'morning'"
)
content = content.replace(
    "    end: Point",
    "    end: Point\n    date: str = '2022-05-30'\n    time_slot: str = 'morning'"
)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
