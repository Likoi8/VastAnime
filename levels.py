# Таблица уровней профиля.
# Формат: (номер_уровня, название, суммарные_часы_для_старта_уровня)
# Уровень 1 начинается с 0 часов. Отредактируйте список, чтобы задать свои пороги и названия.
_TIER_NAMES = [
    "Рамен-новичок",
    "Кицунэ-охотник",
    "Хранитель свитков",
    "Лунная ведьма",
    "Страж тории",
]


def _rank_name(n: int) -> str:
    tier = min((n - 1) // 20, len(_TIER_NAMES) - 1)
    return _TIER_NAMES[tier]


LEVELS = [(n, _rank_name(n), (n - 1) * 20) for n in range(1, 101)]


def get_level_info(total_seconds: int):
    total_hours = total_seconds / 3600
    current = LEVELS[0]
    for lvl in LEVELS:
        if total_hours >= lvl[2]:
            current = lvl
        else:
            break
    idx = current[0] - 1
    next_level = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    if next_level:
        tier_size = next_level[2] - current[2]
        hours_into_tier = total_hours - current[2]
        hours_needed = next_level[2] - total_hours
    else:
        tier_size = 0
        hours_into_tier = 0
        hours_needed = 0
    return {
        "level": current[0],
        "name": current[1],
        "hours_into_tier": round(max(hours_into_tier, 0)),
        "tier_size": round(tier_size),
        "hours_needed": round(max(hours_needed, 0)),
    }
