KEYWORDS = [
    "металлург",
    "гок",
    "сталь",
    "стали",
    "декарбонизац",
    "чугун",
    "прокат",
    "руда",
    "выплавк",
    "доменн",
    "электросталь",
    "ферросплав",
    "горнодобы",
    "обогащен",
    "зелён",
    "зелен",
    "углеродн",
    "esg",
    "metallurg",
    "steel",
    "decarboniz",
    "iron",
    "ore",
    "mining",
    "furnace",
    "smelting",
    "green steel",
    "carbon",
]

EXCLUDE_KEYWORDS = [
    "спорт",
    "футбол",
    "хоккей",
    "криминал",
    "убийство",
    "ограбление",
    "кража",
    "дтп",
    "авария",
    "sports",
    "football",
    "soccer",
    "crime",
    "murder",
    "test",
    "тест",
    "autotranslate",
    "автоперевод",
]

def is_relevant(text):
    if not text:
        return False
    
    text_lower = text.lower()
    
    for exclude in EXCLUDE_KEYWORDS:
        if exclude in text_lower:
            return False
    
    for keyword in KEYWORDS:
        if keyword in text_lower:
            return True
    
    return False

def get_hashtags(text):
    hashtags = []
    text_lower = text.lower()
    
    if any(k in text_lower for k in ["металлург", "metallurg", "сталь", "steel", "чугун", "iron"]):
        hashtags.append("#металлургия")
    
    if any(k in text_lower for k in ["гок", "руда", "ore", "горнодобы", "mining"]):
        hashtags.append("#ГОК")
    
    if any(k in text_lower for k in ["декарбонизац", "decarboniz", "зелён", "green", "углеродн", "carbon"]):
        hashtags.append("#декарбонизация")
    
    if any(k in text_lower for k in ["esg", "устойчив", "sustainab"]):
        hashtags.append("#ESG")
    
    if any(k in text_lower for k in ["инновац", "innovation", "технолог", "technolog"]):
        hashtags.append("#инновации")
    
    if any(k in text_lower for k in ["прокат", "производств", "production"]):
        hashtags.append("#производство")
    
    return hashtags if hashtags else ["#металлургия"]
