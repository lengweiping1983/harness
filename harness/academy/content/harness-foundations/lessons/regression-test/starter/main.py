"""第 28 天：给“应该如此”写一条检查。"""
def validate_steps(value):
    if type(value) is not int or value < 1:
        raise ValueError("invalid-budget")
    return value

assert validate_steps(3) == 3
try:
    validate_steps(0)
except ValueError:
    print("Rejected invalid budget")
else:
    raise AssertionError("The guard did not work")
