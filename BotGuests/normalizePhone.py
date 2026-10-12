"""
Модуль для нормализации и форматирования телефонных номеров.
Приводит ЛЮБОЙ формат к единому виду.
"""

import re


def normalize_phone(phone: str) -> str:
    """
    Приводит ЛЮБОЙ номер к формату 79147018226 (11 цифр, без плюса).
    
    Обрабатывает:
    - +79147018226 → 79147018226
    - 89147018226  → 79147018226 (замена 8 на 7)
    - ++79147018226 → 79147018226
    - +7 (914) 701-82-26 → 79147018226
    - 9147018226 (10 цифр) → 79147018226
    """
    if not phone:
        return ""
    
    # Убираем всё кроме цифр
    digits = re.sub(r'\D', '', str(phone))
    
    # Если 11 цифр и начинается с 8 — меняем на 7
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    
    # Если 10 цифр (без кода страны) — добавляем 7
    if len(digits) == 10:
        digits = '7' + digits
    
    # Если больше 11 цифр — берём последние 11
    if len(digits) > 11:
        digits = digits[-11:]
    
    return digits


def format_phone_pretty(phone: str) -> str:
    """
    Форматирует нормализованный номер в красивый вид: +7 (914) 701-82-26
    """
    digits = normalize_phone(phone)
    if len(digits) == 11 and digits.startswith('7'):
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
    return f"+{digits}" if digits else str(phone)


def format_phone_masked(phone: str) -> str:
    """
    Форматирует номер с маскированием последних цифр (для логов):
    +7 (914) ***-**-26
    """
    digits = normalize_phone(phone)
    if len(digits) == 11 and digits.startswith('7'):
        return f"+{digits[0]} ({digits[1:4]}) ***-**-{digits[9:]}"
    return format_phone_pretty(phone)