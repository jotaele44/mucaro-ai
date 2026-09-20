# Mucaro GIS V1 bounded support matrix

This matrix freezes the V1 GIS claim boundary. Capability outside this matrix is not implied by implementation proximity.

| Domain | V1 supported candidate | Explicit residue/nonclaim |
|---|---|---|
| Vector inspection | GeoJSON FeatureCollection; bounded GeoPackage/Shapefile manifestation inspection | cross-format canonical identity is not inferred |
| Topology | inspect validity; explicit repair with before/after hashes | repair is never implicit |
| Spatial relation | point-in-polygon; bounded spatial join | candidate discovery is not identity proof |
| Geometry comparison | exact/topological comparison, Hausdorff, area/symmetric-difference metrics | tolerance must be declared |
| Overlay | polygon intersection, union, difference, symmetric difference | independent controls required for certification |
| CRS | explicit horizontal pyproj/PROJ transformations with always-XY policy and pipeline identity; exact-source-bound public-domain NOAA Puerto Rico datum grid with frozen manifest/hash/byte/license provenance and no runtime download | additional regional datum grids, independent-engine datum validation, and vertical/time-dependent CRS are not V1-certified |
| Measurement | planar measurements and WGS84 ellipsoidal distance/area | independent control tolerance required |
| Vector formats | GeoJSON plus GeoPackage/Shapefile source-manifest inspection | lossless cross-format conversion parity not claimed |
| Raster | rasterio-readable local raster inspection; GeoTIFF fixture; point sample; bbox clip; transactional nearest/bilinear resampling to explicit dimensions | NODATA != zero; no implicit reprojection; no semantic equivalence claim after resampling |
| Terrain | projected-metre, axis-aligned slope/aspect from complete valid 3x3 neighborhoods; terrain-lineage-bound direct-light hillshade | no smoothing, generalized terrain, curvature, contour, cast-shadow, or measured-radiance claim |
| Hydrology | bounded 8-connected priority-flood depression conditioning with raster-edge/NODATA-adjacent outlets, no breaching or epsilon gradients, explicit maximum fill depth, fill volume accounting, and a two-band conditioned-elevation/fill-depth output; bounded D8 steepest descent on projected horizontal metres with explicit vertical metres, candidate bitmask, accumulation, affine contributing area, and boundary/sink/flat/tie terminal states | no unbounded conditioning, depression breaching, flat routing, watershed/stream extraction, runoff, flood, groundwater, or hydraulic model; conditioning and D8 each reject unsupported CRS, units, transforms, dimensions, and grid conditions rather than inferring them |
| Vector-raster | GeoJSON geometry to raster cell-center analytical intersection and zonal count/sum/min/max/mean/median/population-standard-deviation receipts | no fractional pixel coverage |
| Real vector corpus | byte-frozen bounded Census TIGERweb municipio polygons and USGS earthquake points with authority/query/retrieval/hash/schema/stable-ID/geometry checks | not exhaustive; not a real-raster corpus; mutable service responses are not canonical identity |
| Map/export | output hash + CRS/extent + renderer identity + bound layer hashes | provenance != visual correctness |

## Terminal/residue states

`NULL_EMPTY | NODATA_ONLY | OUTSIDE | OUTSIDE_OR_NO_CELL_CENTER | UNSUPPORTED | UNRESOLVED | FLOW_UNIQUE | BOUNDARY_TERMINAL | SINK_UNRESOLVED | FLAT_UNRESOLVED | TIE_UNRESOLVED` remain distinct where applicable.

## Certification rule

Implementation is not certification. GIS-06 through GIS-10 may be promoted only by denominator events after the frozen independent-control corpus closes with explicit tolerances and zero unexplained mismatch inside the bounded V1 claim.
