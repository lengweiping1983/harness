"""技能选择界面回归使用的独立临时项目。"""
from tests.web_fixture import main


def prepare(root):
    """只写入本次临时项目，不读取用户技能或Agent配置。"""
    for name in ('default-skill', 'writer-skill'):
        path = root/'.harness'/'skills'/name/'SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text(f'---\nname: {name}\ndescription: 技能说明 {name}\n---\n测试技能正文', encoding='utf-8')


if __name__ == '__main__':
    main(enabled=('enable_skills', 'enable_agents'), prepare=prepare)
