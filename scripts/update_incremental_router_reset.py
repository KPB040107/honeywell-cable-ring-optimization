with open('src/resplan_router/phase7_interactive/incremental_router.py', 'r', encoding='utf-8') as f:
    c = f.read()

target = '''        if self._raw_data is not None and getattr(self.plan, "plan_id", "") not in ("slide5", "slide5_schematic"):
            self.load_plan_preview(self._raw_data)
            return self.generate_cable_rings()'''

repl = '''        if self._raw_data is not None and getattr(self.plan, "plan_id", "") not in ("slide5", "slide5_schematic"):
            if "inner" in self._raw_data:
                self.load_plan_preview(self._raw_data)
                return self.generate_cable_rings()
            elif "id" in self._raw_data:
                self.load_dataset_plan(self._raw_data["id"])
                return self.generate_cable_rings()'''

assert target in c.replace('\r\n', '\n'), 'target not found in incremental_router.py'
c = c.replace('\r\n', '\n').replace(target, repl)
with open('src/resplan_router/phase7_interactive/incremental_router.py', 'w', encoding='utf-8') as f:
    f.write(c)
print('Updated incremental_router.py successfully!')
