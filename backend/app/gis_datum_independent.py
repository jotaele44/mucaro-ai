from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio


class IndependentDatumGridError(ValueError):
    """Raised when a bounded horizontal offset grid cannot be evaluated safely."""


def _bilinear(values: np.ndarray, x: float, y: float) -> float:
    x0 = int(np.floor(x))
    y0 = int(np.floor(y))
    x_fraction = x - x0
    y_fraction = y - y0
    return float(
        (1.0 - x_fraction) * (1.0 - y_fraction) * values[y0, x0]
        + x_fraction * (1.0 - y_fraction) * values[y0, x0 + 1]
        + (1.0 - x_fraction) * y_fraction * values[y0 + 1, x0]
        + x_fraction * y_fraction * values[y0 + 1, x0 + 1]
    )


def independently_interpolate_horizontal_offset(
    grid_path: Path,
    *,
    lon: float,
    lat: float,
) -> tuple[float, float]:
    """Apply a bounded NOAA horizontal offset grid without using PROJ.

    The governed TIFF convention stores latitude and longitude offsets in
    arc-seconds in bands 1 and 2. This verifier uses only TIFF decoding and a
    direct cell-center bilinear interpolation; CRS operation selection and
    arbitrary datum transformations remain outside its scope.
    """
    if grid_path.is_symlink() or not grid_path.is_file():
        raise IndependentDatumGridError("grid must be a regular local file")
    try:
        with rasterio.open(grid_path) as source:
            if source.count != 2 or source.width < 2 or source.height < 2:
                raise IndependentDatumGridError("grid must contain two interpolable offset bands")
            if source.tags().get("TYPE") != "HORIZONTAL_OFFSET":
                raise IndependentDatumGridError("grid type is not a horizontal offset grid")
            if source.transform.a <= 0 or source.transform.e >= 0 or source.transform.b != 0 or source.transform.d != 0:
                raise IndependentDatumGridError("grid transform is not north-up and regular")
            step_x = float(source.transform.a)
            step_y = float(-source.transform.e)
            if not np.isclose(step_x, step_y, rtol=0.0, atol=1e-15):
                raise IndependentDatumGridError("grid cells are not square")
            arrays = source.read(out_dtype="float64")
            x = (float(lon) - float(source.transform.c)) / step_x - 0.5
            y = (float(source.transform.f) - float(lat)) / step_y - 0.5
    except IndependentDatumGridError:
        raise
    except (OSError, ValueError, rasterio.errors.RasterioIOError) as exc:
        raise IndependentDatumGridError("grid could not be decoded") from exc

    if not np.isfinite([x, y]).all() or not (0.0 <= x < arrays.shape[2] - 1 and 0.0 <= y < arrays.shape[1] - 1):
        raise IndependentDatumGridError("coordinate is outside the bilinear grid domain")
    shifts_arcseconds = np.array([_bilinear(band, x, y) for band in arrays], dtype="float64")
    if not np.isfinite(shifts_arcseconds).all():
        raise IndependentDatumGridError("grid contains a non-finite shift")
    shift_latitude, shift_longitude = shifts_arcseconds / 3600.0
    return float(lon) + float(shift_longitude), float(lat) + float(shift_latitude)
