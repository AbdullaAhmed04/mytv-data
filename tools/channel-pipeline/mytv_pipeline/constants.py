from __future__ import annotations


UPSTREAM_URLS = {
    "channels": "https://iptv-org.github.io/api/channels.json",
    "feeds": "https://iptv-org.github.io/api/feeds.json",
    "streams": "https://iptv-org.github.io/api/streams.json",
    "logos": "https://iptv-org.github.io/api/logos.json",
    "countries": "https://iptv-org.github.io/api/countries.json",
    "blocklist": "https://iptv-org.github.io/api/blocklist.json",
}

ARAB_COUNTRIES = {
    "YE", "SA", "EG", "IQ", "AE", "KW", "QA", "JO", "LB", "SY", "PS",
    "DZ", "BH", "KM", "DJ", "LY", "MR", "MA", "OM", "SO", "SD", "TN",
}
US_UK = {"US", "GB", "UK"}
COUNTRY_ORDER = [
    "YE", "SA", "EG", "IQ", "AE", "KW", "QA", "JO", "LB", "SY", "PS",
    "DZ", "BH", "KM", "DJ", "LY", "MR", "MA", "OM", "SO", "SD", "TN",
]

ARABIC_COUNTRY_NAMES = {
    "YE": "اليمن", "SA": "السعودية", "EG": "مصر", "IQ": "العراق",
    "AE": "الإمارات", "KW": "الكويت", "QA": "قطر", "JO": "الأردن",
    "LB": "لبنان", "SY": "سوريا", "PS": "فلسطين", "DZ": "الجزائر",
    "BH": "البحرين", "KM": "جزر القمر", "DJ": "جيبوتي", "LY": "ليبيا",
    "MR": "موريتانيا", "MA": "المغرب", "OM": "عُمان", "SO": "الصومال",
    "SD": "السودان", "TN": "تونس", "US": "الولايات المتحدة",
    "GB": "المملكة المتحدة", "UK": "المملكة المتحدة",
}

CATEGORY_MAP = {
    "news": "NEWS",
    "sports": "SPORTS",
    "movies": "MOVIES",
    "series": "SERIES",
    "kids": "KIDS",
    "music": "MUSIC",
    "religious": "RELIGIOUS",
    "documentary": "DOCUMENTARY",
    "culture": "CULTURE",
    "general": "GENERAL",
    "legislative": "GOVERNMENT",
    "public": "GOVERNMENT",
    "government": "GOVERNMENT",
}

OUTPUT_CATEGORY_ORDER = [
    "NEWS", "SPORTS", "MOVIES", "SERIES", "KIDS", "MUSIC", "RELIGIOUS",
    "DOCUMENTARY", "CULTURE", "GENERAL", "GOVERNMENT", "OTHER",
]

ADULT_CATEGORY_WORDS = {
    "adult", "xxx", "porn", "pornography", "erotic", "sex", "18+", "+18",
}
ADULT_NAME_WORDS = {
    "xxx", "porn", "pornhub", "playboy", "hustler", "brazzers", "redlight",
}
