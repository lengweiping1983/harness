def validate_minutes(value):
    if type(value) is not int or value < 0:
        raise ValueError("minutes")
    return value

print(validate_minutes(20))
