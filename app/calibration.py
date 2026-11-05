"""Càlcul compartit entre la web/API i els llindars de notificació."""


def calibrated_values(tank, distance):
    result = {
        "name": tank.name, "capacity_liters": round(tank.capacity_liters, 2),
        "reserve_liters": tank.reserve_liters, "calibration_status": "configured",
        "calibration_mode": "two_point_prototype" if tank.full_distance_mm is not None else
                            "geometry_nominal" if tank.nominal_capacity_liters is not None else "geometry",
        "full_distance_mm": tank.full_distance_mm if tank.full_distance_mm is not None else tank.sensor_to_bottom_mm - tank.height_mm,
        "empty_distance_mm": tank.empty_distance_mm if tank.empty_distance_mm is not None else tank.sensor_to_bottom_mm,
        "geometric_capacity_liters": round(tank.length_mm * tank.width_mm * tank.height_mm / 1_000_000, 2) if tank.length_mm is not None else None,
        "calibration_clamped": False, "level_mm": None, "volume_liters": None, "fill_percent": None,
    }
    if distance is None:
        return result
    if tank.full_distance_mm is not None:
        height = tank.empty_distance_mm - distance
        span = tank.empty_distance_mm - tank.full_distance_mm
        fraction = max(0.0, min(1.0, height / span))
        result.update(level_mm=round(max(0.0, min(span, height)), 1),
                      volume_liters=round(tank.capacity_liters * fraction, 2),
                      fill_percent=round(100 * fraction, 2), calibration_clamped=not 0 <= height <= span)
    else:
        height = tank.sensor_to_bottom_mm - distance
        if 0 <= height <= tank.height_mm:
            result.update(level_mm=round(height, 1),
                          volume_liters=round(tank.capacity_liters * height / tank.height_mm, 2),
                          fill_percent=round(100 * height / tank.height_mm, 2))
        else:
            result["calibration_status"] = "out_of_geometry"
    return result
