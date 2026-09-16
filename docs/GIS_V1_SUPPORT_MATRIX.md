# Mucaro GIS V1 bounded support matrix

This matrix freezes the V1 GIS claim boundary. Capability outside this matrix is not implied by implementation proximity.

| Domain | V1 supported candidate | Explicit residue/nonclaim |
|---|---|---|
| Vector inspection | GeoJSON FeatureCollection; bounded GeoPackage/Shapefile manifestation inspection | cross-format canonical identity is not inferred |
| Topology | inspect validity; explicit repair with before/after hashes | repair is never implicit |
| Spatial relation | point-in-polygon; bounded spatial join | candidate discovery is not identity proof |
| Geometry comparison | exact/topological comparison, Hausdorff, area/symmetric-difference metrics | tolerance must be declared |
| Overlay | polygon intersection, union, difference, symmetric difference | independent controls required for certification |
| CRS | explicit horizontal pyproj/PROJ transformations with always-XY policy and pipeline identity | vertical/time-dependent CRS not V1-certified |
| Measurement | planar measurements and WGS84 ellipsoidal distance/area | independent control tolerance required |
| Vector formats | GeoJSON plus GeoPackage/Shapefile source-manifest inspection | lossless cross-format conversion parity not claimed |
| Raster | rasterio-readable local raster inspection; GeoTIFF fixture; point sample; bbox clip | NODATA != zero; no implicit reprojection |
| Vector-raster | GeoJSON geometry to raster cell-center analytical intersection | no fractional pixel coverage |
| Map/export | output hash + CRS/extent + renderer identity + bound layer hashes | provenance != visual correctness |

## Terminal/residue states

`NULL_EMPTY | NODATA_ONLY | OUTSIDE | OUTSIDE_OR_NO_CELL_CENTER | UNSUPPORTED | UNRESOLVED` remain distinct where applicable.

## Certification rule

Implementation is not certification. GIS-06 through GIS-10 may be promoted only by denominator events after the frozen independent-control corpus closes with explicit tolerances and zero unexplained mismatch inside the bounded V1 claim.
