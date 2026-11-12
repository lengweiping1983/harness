"""第 3 天：把模型放在一个小盒子里。"""
def model_fixture(question):
    # 固定模型替身，不调用在线服务。
    return "Fixture reply to: " + question

question = "Hello, Harness!"
reply = model_fixture(question)
print(reply)
