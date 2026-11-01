def total_minutes(durations):
    total = 0
    for minutes in durations:
        total = total + minutes
    return total

print(total_minutes([10, 20, 5]))
