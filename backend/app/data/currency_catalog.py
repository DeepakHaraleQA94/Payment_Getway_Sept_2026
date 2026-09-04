"""ISO-4217 currency catalog (static reference data).

Single source of truth for currency code -> display name, minor-unit exponent (decimals) and
symbol. Served read-only via GET /api/currencies so the frontend never hardcodes currency lists.
No secrets, no tenant data — pure reference data.
"""

# code: (name, decimals, symbol)
_CATALOG: dict[str, tuple[str, int, str]] = {
    "AED": ("UAE Dirham", 2, "د.إ"),
    "AUD": ("Australian Dollar", 2, "A$"),
    "BDT": ("Bangladeshi Taka", 2, "৳"),
    "BHD": ("Bahraini Dinar", 3, ".د.ب"),
    "BRL": ("Brazilian Real", 2, "R$"),
    "CAD": ("Canadian Dollar", 2, "C$"),
    "CHF": ("Swiss Franc", 2, "CHF"),
    "CLP": ("Chilean Peso", 0, "$"),
    "CNY": ("Chinese Yuan", 2, "¥"),
    "COP": ("Colombian Peso", 2, "$"),
    "CZK": ("Czech Koruna", 2, "Kč"),
    "DKK": ("Danish Krone", 2, "kr"),
    "EGP": ("Egyptian Pound", 2, "E£"),
    "EUR": ("Euro", 2, "€"),
    "GBP": ("British Pound", 2, "£"),
    "HKD": ("Hong Kong Dollar", 2, "HK$"),
    "HUF": ("Hungarian Forint", 2, "Ft"),
    "IDR": ("Indonesian Rupiah", 2, "Rp"),
    "ILS": ("Israeli New Shekel", 2, "₪"),
    "INR": ("Indian Rupee", 2, "₹"),
    "JPY": ("Japanese Yen", 0, "¥"),
    "KES": ("Kenyan Shilling", 2, "KSh"),
    "KRW": ("South Korean Won", 0, "₩"),
    "KWD": ("Kuwaiti Dinar", 3, "د.ك"),
    "LKR": ("Sri Lankan Rupee", 2, "Rs"),
    "MXN": ("Mexican Peso", 2, "$"),
    "MYR": ("Malaysian Ringgit", 2, "RM"),
    "NGN": ("Nigerian Naira", 2, "₦"),
    "NOK": ("Norwegian Krone", 2, "kr"),
    "NZD": ("New Zealand Dollar", 2, "NZ$"),
    "OMR": ("Omani Rial", 3, "ر.ع."),
    "PHP": ("Philippine Peso", 2, "₱"),
    "PKR": ("Pakistani Rupee", 2, "₨"),
    "PLN": ("Polish Zloty", 2, "zł"),
    "PYG": ("Paraguayan Guarani", 0, "₲"),
    "QAR": ("Qatari Riyal", 2, "ر.ق"),
    "RON": ("Romanian Leu", 2, "lei"),
    "RUB": ("Russian Ruble", 2, "₽"),
    "SAR": ("Saudi Riyal", 2, "ر.س"),
    "SEK": ("Swedish Krona", 2, "kr"),
    "SGD": ("Singapore Dollar", 2, "S$"),
    "THB": ("Thai Baht", 2, "฿"),
    "TND": ("Tunisian Dinar", 3, "د.ت"),
    "TRY": ("Turkish Lira", 2, "₺"),
    "TWD": ("New Taiwan Dollar", 2, "NT$"),
    "TZS": ("Tanzanian Shilling", 2, "TSh"),
    "UAH": ("Ukrainian Hryvnia", 2, "₴"),
    "USD": ("US Dollar", 2, "$"),
    "VND": ("Vietnamese Dong", 0, "₫"),
    "XAF": ("Central African CFA Franc", 0, "FCFA"),
    "XOF": ("West African CFA Franc", 0, "CFA"),
    "ZAR": ("South African Rand", 2, "R"),
}


def list_currencies() -> list[dict]:
    """Full ISO-4217 catalog, sorted by code."""
    return [
        {"code": code, "name": name, "decimals": decimals, "symbol": symbol}
        for code, (name, decimals, symbol) in sorted(_CATALOG.items())
    ]
