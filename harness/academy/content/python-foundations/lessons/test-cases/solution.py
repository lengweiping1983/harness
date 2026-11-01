def validate_minutes(value):
    if type(value) is not int or value < 0:
        raise ValueError("minutes")
    return value

def check_validator(candidate):
    assert candidate(25) == 25
    assert candidate(0) == 0
    for value in (-1, True, "20"):
        try:
            candidate(value)
        except ValueError:
            continue
        raise AssertionError("invalid value accepted")

check_validator(validate_minutes)
print("validator checked")
