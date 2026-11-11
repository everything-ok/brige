import re

src = open('main.py', 'r', encoding='utf-8').read()

# 找 HTMLResponse(f""" ... """) 的内容
start = src.find('return HTMLResponse(f"""')
end   = src.find('""")', start + 24)
if start == -1:
    print('未找到 HTMLResponse f-string')
else:
    template = src[start + len('return HTMLResponse(f"""'):end]
    # 用简单占位符格式化，看哪里报错
    try:
        result = template.format(
            site_rows='X',
            model_tags='X',
        )
        print('format OK, length:', len(result))
    except KeyError as e:
        print('KeyError - 未转义的花括号:', e)
        # 找出问题行
        key = str(e).strip("'")
        idx = template.find('{' + key)
        line_no = template[:idx].count('\n') + 1
        print('大约在模板第', line_no, '行')
        print('上下文:', repr(template[max(0,idx-30):idx+50]))
    except Exception as e:
        print('Error:', type(e).__name__, e)
