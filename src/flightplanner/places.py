"""地点匹配：把「伦敦」「London」「LHR」这类输入统一成 IATA 代码。

城市有多个机场时使用城市代码（如伦敦 LON），搜索时会同时查该城市的所有机场。
表里没有的地点，直接输入三字机场代码也可以。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 城市代码 | 中文名（可多个，用 / 分隔） | 英文名（可多个） | 机场
_DATA = """
LON|伦敦|London|LHR,LGW,STN,LTN,LCY,SEN
MAN|曼彻斯特/曼城|Manchester|MAN
LPL|利物浦|Liverpool|LPL
LBA|利兹|Leeds/Bradford|LBA
BHX|伯明翰|Birmingham|BHX
EDI|爱丁堡|Edinburgh|EDI
GLA|格拉斯哥|Glasgow|GLA
BRS|布里斯托|Bristol|BRS
NCL|纽卡斯尔|Newcastle|NCL
EMA|东米德兰兹/诺丁汉|East Midlands/Nottingham|EMA
BFS|贝尔法斯特|Belfast|BFS,BHD
ABZ|阿伯丁|Aberdeen|ABZ
CWL|卡迪夫|Cardiff|CWL
DUB|都柏林|Dublin|DUB
PAR|巴黎|Paris|CDG,ORY,BVA
AMS|阿姆斯特丹|Amsterdam|AMS
BRU|布鲁塞尔|Brussels|BRU,CRL
FRA|法兰克福|Frankfurt|FRA
MUC|慕尼黑|Munich/Munchen|MUC
BER|柏林|Berlin|BER
HAM|汉堡|Hamburg|HAM
DUS|杜塞尔多夫|Dusseldorf/Düsseldorf|DUS
CGN|科隆|Cologne/Koln|CGN
STR|斯图加特|Stuttgart|STR
ZRH|苏黎世|Zurich|ZRH
GVA|日内瓦|Geneva|GVA
BSL|巴塞尔|Basel|BSL
VIE|维也纳|Vienna|VIE
PRG|布拉格|Prague|PRG
BUD|布达佩斯|Budapest|BUD
WAW|华沙|Warsaw|WAW,WMI
KRK|克拉科夫|Krakow/Kraków|KRK
BEG|贝尔格莱德|Belgrade|BEG
ZAG|萨格勒布|Zagreb|ZAG
LJU|卢布尔雅那|Ljubljana|LJU
SJJ|萨拉热窝|Sarajevo|SJJ
TGD|波德戈里察|Podgorica|TGD
TIV|蒂瓦特|Tivat|TIV
SKP|斯科普里|Skopje|SKP
TIA|地拉那|Tirana|TIA
SOF|索非亚|Sofia|SOF
OTP|布加勒斯特|Bucharest|OTP
ATH|雅典|Athens|ATH
SKG|塞萨洛尼基|Thessaloniki|SKG
DBV|杜布罗夫尼克|Dubrovnik|DBV
SPU|斯普利特|Split|SPU
IST|伊斯坦布尔|Istanbul|IST,SAW
ROM|罗马|Rome|FCO,CIA
MIL|米兰|Milan|MXP,LIN,BGY
VCE|威尼斯|Venice|VCE,TSF
FLR|佛罗伦萨|Florence|FLR
NAP|那不勒斯|Naples|NAP
BLQ|博洛尼亚|Bologna|BLQ
PSA|比萨|Pisa|PSA
MAD|马德里|Madrid|MAD
BCN|巴塞罗那|Barcelona|BCN
AGP|马拉加|Malaga|AGP
PMI|马略卡/帕尔马|Palma/Mallorca|PMI
SVQ|塞维利亚|Seville|SVQ
VLC|瓦伦西亚|Valencia|VLC
LIS|里斯本|Lisbon|LIS
OPO|波尔图|Porto|OPO
FAO|法鲁|Faro|FAO
NCE|尼斯|Nice|NCE
LYS|里昂|Lyon|LYS
MRS|马赛|Marseille|MRS
CPH|哥本哈根|Copenhagen|CPH
STO|斯德哥尔摩|Stockholm|ARN,BMA,NYO
OSL|奥斯陆|Oslo|OSL
HEL|赫尔辛基|Helsinki|HEL
RIX|里加|Riga|RIX
TLL|塔林|Tallinn|TLL
VNO|维尔纽斯|Vilnius|VNO
KEF|雷克雅未克|Reykjavik|KEF
MLA|马耳他|Malta|MLA
LCA|拉纳卡/塞浦路斯|Larnaca/Cyprus|LCA
DXB|迪拜|Dubai|DXB,DWC
AUH|阿布扎比|Abu Dhabi|AUH
DOH|多哈|Doha|DOH
CAI|开罗|Cairo|CAI
RAK|马拉喀什|Marrakech|RAK
TLV|特拉维夫|Tel Aviv|TLV
BJS|北京|Beijing|PEK,PKX
SHA|上海|Shanghai|PVG,SHA
CAN|广州|Guangzhou|CAN
SZX|深圳|Shenzhen|SZX
CTU|成都|Chengdu|CTU,TFU
CKG|重庆|Chongqing|CKG
HGH|杭州|Hangzhou|HGH
NKG|南京|Nanjing|NKG
XMN|厦门|Xiamen|XMN
WUH|武汉|Wuhan|WUH
XIY|西安|Xi'an/Xian|XIY
CSX|长沙|Changsha|CSX
KMG|昆明|Kunming|KMG
TAO|青岛|Qingdao|TAO
DLC|大连|Dalian|DLC
SHE|沈阳|Shenyang|SHE
HRB|哈尔滨|Harbin|HRB
TSN|天津|Tianjin|TSN
CGO|郑州|Zhengzhou|CGO
FOC|福州|Fuzhou|FOC
NNG|南宁|Nanning|NNG
HAK|海口|Haikou|HAK
SYX|三亚|Sanya|SYX
URC|乌鲁木齐|Urumqi|URC
HKG|香港|Hong Kong|HKG
MFM|澳门|Macau|MFM
TPE|台北|Taipei|TPE,TSA
TYO|东京|Tokyo|HND,NRT
OSA|大阪|Osaka|KIX,ITM
SPK|札幌|Sapporo|CTS
FUK|福冈|Fukuoka|FUK
SEL|首尔|Seoul|ICN,GMP
PUS|釜山|Busan|PUS
BKK|曼谷|Bangkok|BKK,DMK
HKT|普吉|Phuket|HKT
CNX|清迈|Chiang Mai|CNX
SIN|新加坡|Singapore|SIN
KUL|吉隆坡|Kuala Lumpur|KUL
DPS|巴厘岛|Bali/Denpasar|DPS
CGK|雅加达|Jakarta|CGK
MNL|马尼拉|Manila|MNL
SGN|胡志明市|Ho Chi Minh City/Saigon|SGN
HAN|河内|Hanoi|HAN
DEL|德里|Delhi|DEL
BOM|孟买|Mumbai|BOM
MLE|马尔代夫|Maldives/Male|MLE
CMB|科伦坡|Colombo|CMB
KTM|加德满都|Kathmandu|KTM
NYC|纽约|New York|JFK,EWR,LGA
LAX|洛杉矶|Los Angeles|LAX
SFO|旧金山|San Francisco|SFO
CHI|芝加哥|Chicago|ORD,MDW
BOS|波士顿|Boston|BOS
WAS|华盛顿|Washington|IAD,DCA
SEA|西雅图|Seattle|SEA
MIA|迈阿密|Miami|MIA
LAS|拉斯维加斯|Las Vegas|LAS
YTO|多伦多|Toronto|YYZ
YVR|温哥华|Vancouver|YVR
YMQ|蒙特利尔|Montreal|YUL
MEX|墨西哥城|Mexico City|MEX
CUN|坎昆|Cancun|CUN
SAO|圣保罗|Sao Paulo|GRU
SYD|悉尼|Sydney|SYD
MEL|墨尔本|Melbourne|MEL
AKL|奥克兰|Auckland|AKL
JNB|约翰内斯堡|Johannesburg|JNB
CPT|开普敦|Cape Town|CPT
NBO|内罗毕|Nairobi|NBO
"""


@dataclass(frozen=True)
class Place:
    code: str
    zh: tuple[str, ...]
    en: tuple[str, ...]
    airports: tuple[str, ...]

    @property
    def label(self) -> str:
        airports = "、".join(self.airports)
        return f"{self.zh[0]} {self.en[0]}（{airports}）"

    def to_view(self) -> dict:
        return {"code": self.code, "name": self.zh[0], "name_en": self.en[0], "airports": list(self.airports), "label": self.label}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode() if s.isascii() is False and not re.search(r"[一-鿿]", s) else s
    return re.sub(r"[\s'’.\-]", "", s).lower()


PLACES: list[Place] = []
for _line in _DATA.strip().splitlines():
    _code, _zh, _en, _airports = _line.split("|")
    PLACES.append(Place(_code, tuple(_zh.split("/")), tuple(_en.split("/")), tuple(_airports.split(","))))

BY_CODE: dict[str, Place] = {p.code: p for p in PLACES}
AIRPORT_TO_PLACE: dict[str, Place] = {a: p for p in PLACES for a in p.airports}
_BY_NAME: dict[str, Place] = {}
for _p in PLACES:
    for _name in _p.zh + _p.en:
        _BY_NAME[_norm(_name)] = _p
        if not _name.isascii():
            # 中文名去掉「市」也能匹配
            _BY_NAME[_norm(_name.removesuffix("市"))] = _p


# 具体机场的常用叫法，输入「伦敦希思罗」「上海浦东」时匹配到单个机场
AIRPORT_ALIASES: dict[str, str] = {
    "希思罗": "LHR", "heathrow": "LHR", "盖特威克": "LGW", "gatwick": "LGW", "斯坦斯特德": "STN", "stansted": "STN",
    "卢顿": "LTN", "luton": "LTN", "城市机场": "LCY", "londoncity": "LCY", "绍森德": "SEN", "southend": "SEN",
    "戴高乐": "CDG", "charlesdegaulle": "CDG", "奥利": "ORY", "orly": "ORY", "博韦": "BVA", "beauvais": "BVA",
    "浦东": "PVG", "pudong": "PVG", "虹桥": "SHA", "hongqiao": "SHA", "首都": "PEK", "capital": "PEK", "大兴": "PKX", "daxing": "PKX",
    "天府": "TFU", "tianfu": "TFU", "双流": "CTU", "shuangliu": "CTU", "白云": "CAN", "baiyun": "CAN", "宝安": "SZX", "baoan": "SZX",
    "成田": "NRT", "narita": "NRT", "羽田": "HND", "haneda": "HND", "关西": "KIX", "kansai": "KIX", "仁川": "ICN", "incheon": "ICN", "金浦": "GMP", "gimpo": "GMP",
    "桃园": "TPE", "taoyuan": "TPE", "松山": "TSA", "songshan": "TSA", "素万那普": "BKK", "suvarnabhumi": "BKK", "廊曼": "DMK", "donmueang": "DMK",
    "肯尼迪": "JFK", "jfk": "JFK", "纽瓦克": "EWR", "newark": "EWR", "拉瓜迪亚": "LGA", "laguardia": "LGA",
    "菲乌米奇诺": "FCO", "fiumicino": "FCO", "马尔彭萨": "MXP", "malpensa": "MXP", "利纳特": "LIN", "linate": "LIN",
    "萨比哈": "SAW", "sabiha": "SAW", "阿兰达": "ARN", "arlanda": "ARN",
}


class UnknownPlace(ValueError):
    def __init__(self, text: str, suggestions: list[Place], message: str | None = None):
        self.text = text
        self.suggestions = suggestions
        hint = "；你是不是要找：" + "、".join(p.label for p in suggestions[:5]) if suggestions else "；可以直接输入三字机场代码"
        super().__init__(message or f"不认识的地点「{text}」{hint}")


# 国家名不能直接当地点用（一个国家有很多机场），提示改填城市
COUNTRIES: dict[str, tuple[str, ...]] = {
    "英国/uk/unitedkingdom/britain/greatbritain/england/英格兰": ("LON", "MAN", "BHX", "EDI"),
    "苏格兰/scotland": ("EDI", "GLA", "ABZ"),
    "爱尔兰/ireland": ("DUB",),
    "法国/france": ("PAR", "NCE", "LYS"),
    "德国/germany": ("BER", "FRA", "MUC"),
    "意大利/italy": ("ROM", "MIL", "VCE"),
    "西班牙/spain": ("MAD", "BCN"),
    "塞尔维亚/serbia": ("BEG",),
    "波黑/波斯尼亚/bosnia/bosniaandherzegovina": ("SJJ",),
    "黑山/montenegro": ("TIV", "TGD"),
    "克罗地亚/croatia": ("DBV", "SPU", "ZAG"),
    "中国/china": ("BJS", "SHA", "CAN"),
    "日本/japan": ("TYO", "OSA"),
    "美国/usa/us/unitedstates/america": ("NYC", "LAX", "SFO"),
}
_COUNTRY_BY_NAME: dict[str, tuple[str, tuple[str, ...]]] = {
    n: (names.split("/")[0], codes) for names, codes in COUNTRIES.items() for n in names.split("/")
}


def search_places(text: str, limit: int = 8) -> list[Place]:
    """模糊查找，用于输入框自动补全。"""
    q = _norm(text)
    if not q:
        return []
    exact, prefix, contains = [], [], []
    for p in PLACES:
        names = [_norm(n) for n in p.zh + p.en] + [p.code.lower()] + [a.lower() for a in p.airports]
        if q in names:
            exact.append(p)
        elif any(n.startswith(q) for n in names):
            prefix.append(p)
        elif len(q) >= 2 and any(q in n for n in names):
            contains.append(p)
    return (exact + prefix + contains)[:limit]


def resolve(text: str) -> str:
    """把输入变成 IATA 代码（城市代码或机场代码）。"""
    raw = text.strip()
    if not raw:
        raise UnknownPlace(text, [])
    q = _norm(raw)
    if q in _BY_NAME:
        return _BY_NAME[q].code
    if q in _COUNTRY_BY_NAME:
        country, codes = _COUNTRY_BY_NAME[q]
        cities = [BY_CODE[c] for c in codes if c in BY_CODE]
        names = "、".join(c.zh[0] for c in cities) or "城市名"
        raise UnknownPlace(raw, cities, f"「{raw}」是国家，请填具体城市或机场，比如 {names}")
    upper = raw.upper()
    if re.fullmatch(r"[A-Z]{3}", upper):
        return upper
    # 「伦敦希思罗」「London Heathrow」：先认具体机场，再认城市名
    for alias, airport in sorted(AIRPORT_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if _norm(alias) in q:
            return airport
    for name, place in sorted(_BY_NAME.items(), key=lambda kv: -len(kv[0])):
        if len(name) >= 2 and name in q:
            return place.code
    raise UnknownPlace(raw, search_places(raw[:2]) if len(raw) >= 2 else [])


def airports_for(code: str) -> list[str]:
    """城市代码 → 该城市所有机场；机场代码 → 自己。"""
    code = code.upper()
    place = BY_CODE.get(code)
    return list(place.airports) if place else [code]


def same_place(a: str, b: str) -> bool:
    """两个代码是否指同一城市（如 LON 和 LHR）。"""
    pa = BY_CODE.get(a.upper()) or AIRPORT_TO_PLACE.get(a.upper())
    pb = BY_CODE.get(b.upper()) or AIRPORT_TO_PLACE.get(b.upper())
    return a.upper() == b.upper() or (pa is not None and pa is pb)


def display_name(code: str) -> str:
    code = code.upper()
    place = BY_CODE.get(code) or AIRPORT_TO_PLACE.get(code)
    if not place:
        return code
    return place.zh[0] if place.code == code else f"{place.zh[0]} {code}"


# 航司：中文名 / 英文名 → IATA 二字代码
_AIRLINES = """
BA|英航/英国航空|British Airways
VS|维珍/维珍航空|Virgin Atlantic
U2|易捷/易捷航空|easyJet
FR|瑞安/瑞安航空|Ryanair
LS|Jet2|Jet2
W6|威兹/威兹航空|Wizz Air
W9|威兹英国|Wizz Air UK
EI|爱尔兰航空|Aer Lingus
AF|法航/法国航空|Air France
KL|荷航/荷兰皇家航空|KLM
LH|汉莎/汉莎航空|Lufthansa
LX|瑞士航空/瑞航|Swiss
OS|奥地利航空|Austrian
SN|布鲁塞尔航空|Brussels Airlines
IB|西班牙国家航空/伊比利亚|Iberia
VY|伏林/伏林航空|Vueling
TP|葡航/葡萄牙航空|TAP Air Portugal
AZ|意航/ITA航空|ITA Airways
SK|北欧航空|SAS
AY|芬航/芬兰航空|Finnair
LO|波兰航空/波航|LOT
JU|塞尔维亚航空|Air Serbia
TK|土航/土耳其航空|Turkish Airlines
PC|飞马航空|Pegasus
EK|阿联酋/阿联酋航空|Emirates
QR|卡航/卡塔尔航空|Qatar Airways
EY|阿提哈德/阿提哈德航空|Etihad
CA|国航/中国国际航空|Air China
MU|东航/中国东方航空|China Eastern
CZ|南航/中国南方航空|China Southern
HU|海航/海南航空|Hainan Airlines
3U|川航/四川航空|Sichuan Airlines
CX|国泰/国泰航空|Cathay Pacific
SQ|新航/新加坡航空|Singapore Airlines
JL|日航/日本航空|Japan Airlines
NH|全日空|ANA/All Nippon
KE|大韩/大韩航空|Korean Air
OZ|韩亚/韩亚航空|Asiana
TG|泰航/泰国国际航空|Thai Airways
AA|美航/美国航空|American Airlines
UA|美联航/联合航空|United
DL|达美/达美航空|Delta
AC|加航/加拿大航空|Air Canada
QF|澳航/澳洲航空|Qantas
"""

AIRLINES: dict[str, tuple[str, str]] = {}
_AIRLINE_BY_NAME: dict[str, str] = {}
for _line in _AIRLINES.strip().splitlines():
    _code, _zh, _en = _line.split("|")
    AIRLINES[_code] = (_zh.split("/")[0], _en.split("/")[0])
    for _name in _zh.split("/") + _en.split("/") + [_code]:
        _AIRLINE_BY_NAME[_norm(_name)] = _code


def resolve_airline(text: str) -> str:
    """「英航」「British Airways」「BA」→ BA。不认识的二字代码原样返回。"""
    q = _norm(text)
    if q in _AIRLINE_BY_NAME:
        return _AIRLINE_BY_NAME[q]
    upper = text.strip().upper()
    if re.fullmatch(r"[A-Z0-9]{2}", upper):
        return upper
    raise ValueError(f"不认识的航司「{text}」，可以输入二字代码，如 BA、JU")


def parse_airlines(text: str | None) -> list[str]:
    if not text:
        return []
    codes: list[str] = []
    for part in (p.strip() for p in re.split(r"[,，、/;；]+", text)):
        if not part:
            continue
        try:
            codes.append(resolve_airline(part))  # 「Air Serbia」这种带空格的整体名称
        except ValueError:
            codes += [resolve_airline(w) for w in part.split()]
    return list(dict.fromkeys(codes))
