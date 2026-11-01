"""第 25 天：需要时再拿一本操作手册。"""
skills = {"summarize": "Read the text; keep the main point; cite the source."}

def load_skill(name):
    if name not in skills:
        raise ValueError("unknown-skill")
    return skills[name]

print(load_skill("summarize"))
