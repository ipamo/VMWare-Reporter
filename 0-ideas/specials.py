

def detect_specials(obj: vim.ManagedObject, expected: dict[str,Any], attr_prefix: str = None, use_full_attr: bool = False):
    results = None

    for attr, expected_value in expected.items():
        full_attr = (f'{attr_prefix}.' if attr_prefix else '') + attr
        value = get_obj_attr(obj, attr)

        if isinstance(expected_value, dict) and isinstance(value, vim.ManagedObject):
            result = detect_specials(value, expected_value, attr_prefix=full_attr)
        elif value != expected_value:
            result = f'{full_attr if use_full_attr else attr}: {value}'
        else:
            continue

        results = (f'\n{results}' if results else '') + result

    return results
