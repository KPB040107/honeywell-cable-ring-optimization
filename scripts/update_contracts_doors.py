with open('src/resplan_router/contracts.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''    obstacles_wkt: str = Field(
        description="WKT geometry of obstacle union O = wall + window + front_door"
    )
    rooms: Dict[str, EligibleOrExcludedRoom] = Field(
        default_factory=dict,
        description="Dictionary mapping room_id to room models",
    )
    nodes: Dict[str, FixedNode] = Field(
        default_factory=dict,
        description="Dictionary mapping node_id to fixed nodes",
    )

    @field_validator("inner_wkt", "free_space_wkt", "obstacles_wkt")
    @classmethod
    def validate_wkt_strings(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("WKT string must not be empty")
        try:
            shapely.from_wkt(v)
        except Exception as exc:
            raise ValueError(f"Invalid Shapely WKT geometry: {exc}") from exc
        return v

    @property
    def inner_geom(self) -> shapely.Polygon:
        return shapely.from_wkt(self.inner_wkt)

    @property
    def free_space_geom(self) -> shapely.MultiPolygon:
        return shapely.from_wkt(self.free_space_wkt)

    @property
    def obstacles_geom(self) -> shapely.MultiPolygon:
        return shapely.from_wkt(self.obstacles_wkt)'''

replacement = '''    obstacles_wkt: str = Field(
        description="WKT geometry of obstacle union O = wall + window + front_door"
    )
    doors_wkt: Optional[str] = Field(
        default=None,
        description="Optional WKT geometry of metric interior doors",
    )
    front_doors_wkt: Optional[str] = Field(
        default=None,
        description="Optional WKT geometry of front/exterior doors",
    )
    rooms: Dict[str, EligibleOrExcludedRoom] = Field(
        default_factory=dict,
        description="Dictionary mapping room_id to room models",
    )
    nodes: Dict[str, FixedNode] = Field(
        default_factory=dict,
        description="Dictionary mapping node_id to fixed nodes",
    )

    @field_validator("inner_wkt", "free_space_wkt", "obstacles_wkt")
    @classmethod
    def validate_wkt_strings(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("WKT string must not be empty")
        try:
            shapely.from_wkt(v)
        except Exception as exc:
            raise ValueError(f"Invalid Shapely WKT geometry: {exc}") from exc
        return v

    @field_validator("doors_wkt", "front_doors_wkt")
    @classmethod
    def validate_optional_wkt_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            shapely.from_wkt(v)
        except Exception as exc:
            raise ValueError(f"Invalid Shapely WKT geometry: {exc}") from exc
        return v

    @property
    def inner_geom(self) -> shapely.Polygon:
        return shapely.from_wkt(self.inner_wkt)

    @property
    def free_space_geom(self) -> shapely.MultiPolygon:
        return shapely.from_wkt(self.free_space_wkt)

    @property
    def obstacles_geom(self) -> shapely.MultiPolygon:
        return shapely.from_wkt(self.obstacles_wkt)

    @property
    def doors_geom(self) -> Optional[BaseGeometry]:
        if self.doors_wkt:
            return shapely.from_wkt(self.doors_wkt)
        return None

    @property
    def front_doors_geom(self) -> Optional[BaseGeometry]:
        if self.front_doors_wkt:
            return shapely.from_wkt(self.front_doors_wkt)
        return None'''

norm_content = content.replace('\r\n', '\n')
norm_target = target.replace('\r\n', '\n')
norm_replacement = replacement.replace('\r\n', '\n')

assert norm_target in norm_content, 'Target not found in content!'
norm_content = norm_content.replace(norm_target, norm_replacement)

with open('src/resplan_router/contracts.py', 'w', encoding='utf-8') as f:
    f.write(norm_content)
print('Updated contracts.py successfully!')
