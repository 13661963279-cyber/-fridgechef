import os
import json
import re
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB max
CORS(app)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """你是一个专业中餐厨师。你会收到用户的食材列表，你需要推荐3道菜。

你必须只输出一行合法的JSON数组，不能用```json```包裹，不能有任何解释文字。

每道菜的JSON对象必须包含以下全部字段：
- name: 菜名
- difficulty: 难度（简单/中等/挑战）
- cooking_time: 预计总时间
- my_ingredients: 用到的已有食材列表
- need_to_buy: 需要额外购买的食材列表
- ingredients_detail: [{name, amount, note}] 食材及用量
- steps: [{step, action, seasoning, heat, duration}] 烹饪步骤，每步必须写明调味料克数、火候、时间
- nutrition: {calories, protein, carbs, fat} 营养估算
- tips: 烹饪小贴士

示例格式：
[{"name":"番茄炒蛋","difficulty":"简单","cooking_time":"15分钟","my_ingredients":["鸡蛋","番茄"],"need_to_buy":["葱"],"ingredients_detail":[{"name":"鸡蛋","amount":"3个","note":"打散"},{"name":"番茄","amount":"2个","note":"切块"}],"steps":[{"step":1,"action":"热锅倒油","seasoning":"食用油15ml","heat":"大火","duration":"30秒"},{"step":2,"action":"倒入蛋液炒散","seasoning":"盐1g","heat":"中火","duration":"1分钟"}],"nutrition":{"calories":"260大卡","protein":"14g","carbs":"5g","fat":"18g"},"tips":"鸡蛋不要炒太老，嫩滑才好吃。"}]"""


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/recipes", methods=["POST"])
def generate_recipes():
    data = request.get_json()
    if not data or "ingredients" not in data:
        return jsonify({"error": "请提供食材列表"}), 400

    ingredients = data["ingredients"]
    if not ingredients:
        return jsonify({"error": "食材列表为空"}), 400

    if not ANTHROPIC_API_KEY:
        return jsonify({
            "recipes": mock_recipes(ingredients),
            "note": "未配置 ANTHROPIC_API_KEY，返回示例数据"
        })

    ingredient_names = [i.get("name", i) if isinstance(i, dict) else i for i in ingredients]
    prompt = f"我家里有：{'、'.join(ingredient_names)}。不用全用完，挑几样搭配，推荐3道菜。"

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                temperature=0.3,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            text_blocks = [b.text for b in response.content if b.type == "text"]
            text = text_blocks[0] if text_blocks else ""

            recipes = parse_recipes(text)
            if recipes and recipes[0].get("name") != "解析失败":
                return jsonify({"recipes": recipes})

        except Exception as e:
            if attempt == 2:
                return jsonify({"error": f"AI 生成失败: {str(e)}"}), 500

    return jsonify({
        "recipes": mock_recipes(ingredients),
        "note": "多次解析失败，返回示例数据"
    })


SCAN_SYSTEM_PROMPT = "你是一个食材识别助手。用户发给你一张冰箱/厨房的照片，请你列出图片中能看到的每一件食材。格式：食材名(数量)，每行一个。"


@app.route("/scan", methods=["POST"])
def scan_ingredients():
    data = request.get_json()
    if not data or "image" not in data:
        return jsonify({"error": "请提供图片"}), 400

    image_b64 = data["image"]
    media_type = "image/jpeg"
    if image_b64.startswith("data:"):
        m = re.match(r"data:(image/\w+);", image_b64)
        if m:
            media_type = m.group(1)
        image_b64 = image_b64.split(",", 1)[1]

    if not ANTHROPIC_API_KEY:
        return jsonify({"ingredients": [
            {"name": "鸡蛋", "amount": "6个"},
            {"name": "番茄", "amount": "3个"},
            {"name": "青菜", "amount": "1把"},
        ], "note": "未配置 API Key，返回示例数据"})

    try:
        base64.b64decode(image_b64)

        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            temperature=0,
            system=SCAN_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": "图片里有哪些食材？"},
                ],
            }],
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        text = text_blocks[0] if text_blocks else ""
        ingredients = parse_scan_result(text)
        return jsonify({"ingredients": ingredients})

    except Exception as e:
        return jsonify({"error": f"识别失败: {str(e)}"}), 500


def parse_scan_result(text):
    """Parse ingredient list from line-by-line or JSON format."""
    ingredients = []

    # Try JSON first
    json_result = parse_recipes(text)
    if json_result and json_result[0].get("name") != "解析失败":
        return json_result

    reject_keywords = [
        "无法识别", "抱歉", "不能", "看不清", "重新上传", "图片内容",
        "看不到", "未能检测", "无法确定", "无法判断", "I cannot",
        "I can't", "unable to", "not able to",
    ]
    text_lower = text.lower()
    if any(kw in text_lower for kw in reject_keywords):
        return [{
            "name": "识别失败",
            "amount": "请确保照片清晰、光线充足，尽量拍食材本身而非包装",
        }]

    for line in text.strip().split("\n"):
        line = line.strip().rstrip("。，,;；")
        if not line or len(line) < 2 or len(line) > 50:
            continue
        line = re.sub(r"^[\d]+[\.\、\)]\s*", "", line)
        line = line.lstrip("-·• ")

        match = re.match(r"^(.+?)[\(（]([^\)）]+)[\)）]$", line)
        if match:
            name, amount = match.group(1).strip(), match.group(2).strip()
            if len(name) <= 20:
                ingredients.append({"name": name, "amount": amount})
            continue

        parts = re.split(r"[，,\s]{2,}", line)
        if len(parts) >= 2 and len(parts[0]) <= 20:
            ingredients.append({"name": parts[0].strip(), "amount": parts[1].strip()})
            continue

        if 2 <= len(line) <= 20:
            ingredients.append({"name": line.strip(), "amount": "适量"})

    if ingredients:
        return ingredients
    return [{
        "name": "识别失败",
        "amount": "AI 未能从图片中检测到食材，请换一张清晰的照片重试",
    }]


def parse_recipes(text):
    """Extract and parse JSON array from AI response."""
    text = text.strip()

    # Remove ```json / ``` wrappers if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?\s*```\s*$", "", text)

    # Find the JSON array boundaries
    start = text.find("[")
    end = text.rfind("]")

    if start == -1 or end <= start:
        return [{"name": "解析失败", "raw": text[:300]}]

    json_str = text[start:end + 1]

    # Try direct parse
    try:
        result = json.loads(json_str)
        if isinstance(result, list) and len(result) > 0:
            return result
    except json.JSONDecodeError:
        pass

    # Try fixing common issues
    fixes = [
        re.sub(r",\s*([}\]])", r"\1", json_str),  # trailing commas
        re.sub(r'"\s*\n\s*"', '","', json_str),  # strings split across lines
    ]

    for fixed in fixes:
        try:
            result = json.loads(fixed)
            if isinstance(result, list) and len(result) > 0:
                return result
        except json.JSONDecodeError:
            continue

    # Last resort: regex extract individual recipe objects
    objects = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', json_str)
    results = []
    for obj_str in objects:
        try:
            obj = json.loads(obj_str)
            if "name" in obj:
                results.append(obj)
        except json.JSONDecodeError:
            continue

    if results:
        return results

    return [{"name": "解析失败", "raw": json_str[:300]}]


def mock_recipes(ingredients):
    ingredient_names = [i.get("name", str(i)) for i in ingredients] if isinstance(ingredients[0], dict) else ingredients
    ingredient_str = "、".join(ingredient_names[:5])

    return [
        {
            "name": f"{ingredient_str}炒饭" if ingredient_names else "蛋炒饭",
            "difficulty": "简单",
            "cooking_time": "15分钟",
            "my_ingredients": ingredient_names[:3],
            "need_to_buy": ["葱"],
            "ingredients_detail": [
                {"name": "米饭", "amount": "300g", "note": "隔夜饭更好"},
                {"name": "鸡蛋", "amount": "2个", "note": "打散备用"},
                {"name": ingredient_names[0] if ingredient_names else "胡萝卜", "amount": "100g", "note": "切丁"},
                {"name": "葱", "amount": "10g", "note": "切葱花"},
            ],
            "steps": [
                {"step": 1, "action": "热锅倒油", "seasoning": "食用油15ml", "heat": "大火", "duration": "30秒"},
                {"step": 2, "action": "倒入蛋液炒散", "seasoning": "盐1g", "heat": "中火", "duration": "1分钟"},
                {"step": 3, "action": "加入米饭和配菜翻炒", "seasoning": "生抽10ml、盐2g", "heat": "大火", "duration": "3分钟"},
                {"step": 4, "action": "撒葱花出锅", "seasoning": "白胡椒粉少许", "heat": "关火", "duration": "10秒"},
            ],
            "nutrition": {"calories": "450大卡", "protein": "14g", "carbs": "55g", "fat": "18g"},
            "tips": "隔夜饭水分少，炒出来粒粒分明，口感更好。",
        },
        {
            "name": f"清炒{ingredient_names[0] if ingredient_names else '时蔬'}",
            "difficulty": "简单",
            "cooking_time": "10分钟",
            "my_ingredients": ingredient_names[:2],
            "need_to_buy": ["蒜"],
            "ingredients_detail": [
                {"name": ingredient_names[0] if ingredient_names else "青菜", "amount": "300g", "note": "洗净切段"},
                {"name": "蒜", "amount": "3瓣", "note": "切片"},
            ],
            "steps": [
                {"step": 1, "action": "热锅倒油，爆香蒜片", "seasoning": "食用油15ml", "heat": "中火", "duration": "30秒"},
                {"step": 2, "action": "放入食材大火翻炒", "seasoning": "盐3g", "heat": "大火", "duration": "2分钟"},
                {"step": 3, "action": "加少许水焖一下", "seasoning": "蚝油5ml", "heat": "中火", "duration": "1分钟"},
                {"step": 4, "action": "收汁出锅", "seasoning": "无", "heat": "大火", "duration": "30秒"},
            ],
            "nutrition": {"calories": "120大卡", "protein": "4g", "carbs": "10g", "fat": "8g"},
            "tips": "青菜不要炒太久，保持脆嫩口感是关键。",
        },
        {
            "name": f"{ingredient_names[-1] if ingredient_names else '食材'}炖汤",
            "difficulty": "中等",
            "cooking_time": "40分钟",
            "my_ingredients": ingredient_names[:3],
            "need_to_buy": ["姜", "枸杞"],
            "ingredients_detail": [
                {"name": ingredient_names[0] if ingredient_names else "排骨", "amount": "300g", "note": "焯水去血沫"},
                {"name": "姜", "amount": "3片", "note": ""},
                {"name": "盐", "amount": "5g", "note": "最后调味"},
            ],
            "steps": [
                {"step": 1, "action": "食材冷水下锅焯水", "seasoning": "料酒10ml", "heat": "大火", "duration": "5分钟"},
                {"step": 2, "action": "捞出冲洗干净，换清水", "seasoning": "无", "heat": "—", "duration": "1分钟"},
                {"step": 3, "action": "加入姜片，大火烧开转小火慢炖", "seasoning": "无", "heat": "小火", "duration": "30分钟"},
                {"step": 4, "action": "加盐调味出锅", "seasoning": "盐5g", "heat": "关火", "duration": "10秒"},
            ],
            "nutrition": {"calories": "280大卡", "protein": "25g", "carbs": "8g", "fat": "16g"},
            "tips": "炖汤一定要小火慢炖，汤才会清澈鲜美。",
        },
    ]


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
