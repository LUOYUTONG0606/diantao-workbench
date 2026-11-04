"""点淘任务的明确排除项；只按任务名称判断，不操作设备。"""
import re
import unicodedata


def is_blocked_boutique_task(name):
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", text)
    if "精选宝贝" in text:
        return True
    # 同一类商品任务会改名为特惠/优惠，或被 OCR 截成“查看3个”。
    product = re.search(r"精选|优选|特惠|优惠|宝贝|商品|好物", text)
    count = re.search(r"(?:查看|看|点击|点开|浏览)(?:3|三)(?:个|件|次)", text)
    if count:
        suffix = text[count.end():]
        if product or re.fullmatch(r"(?:[（(]\d+/\d+[）)])?", suffix):
            return True
    if product and re.search(r"(?:查看|看|点击|点开|浏览).{0,16}(?:3|三)次", text):
        return True
    return bool(re.search(r"(?:精选|优选|特惠|优惠).*?(?:宝贝|商品)", text)
                and re.search(r"[（(]\d+/3[）)]", text))
