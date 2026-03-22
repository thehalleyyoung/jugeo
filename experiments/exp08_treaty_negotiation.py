#!/usr/bin/env python3
"""Paper 8 Experiment -- Automated Interface Reconciliation for Modular Verification.

Hypothesis: JuGeo treaty negotiation resolves modular verification conflicts
through geometric convergence -- conflicts decrease monotonically through
negotiation rounds.

Methodology: Write pairs of interacting modules, run jugeo prove individually
and combined, jugeo equiv for interface compatibility. Measure obstruction
convergence and compare eager vs exhaustive vs iterative strategies.

Every number is produced by the jugeo CLI (subprocess).
Re-run: python3 experiments/exp08_treaty_negotiation.py
"""
import subprocess, json, os, tempfile, time, random, ast, statistics

random.seed(42)
ROOT = os.path.join(os.path.dirname(__file__), "..")

# -- CLI helpers -----------------------------------------------------------

def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects


def write_temp_py(source):
    """Write source to a temp .py file, return path."""
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


LITERATURE_BASELINES = {
    "ML_module_signatures": {
        "description": "ML module signatures are static -- no negotiation",
        "negotiation_rounds": 0,
        "dynamic_resolution": False,
        "cite": "Harper & Lillibridge, POPL 1994",
    },
    "Haskell_type_classes": {
        "description": "Haskell type classes: coherence checking, no dynamic negotiation",
        "negotiation_rounds": 0,
        "dynamic_resolution": False,
        "cite": "Wadler & Blott, POPL 1989",
    },
    "Fstar_interfaces": {
        "description": "F* interfaces: module-level type checking only",
        "negotiation_rounds": 0,
        "dynamic_resolution": False,
        "cite": "Swamy et al., POPL 2016",
    },
}

# -- 50 Module Pairs ------------------------------------------------------

PAIRS = {
    "email_validator_formatter": {
        "module_a": '''def validate_email(email):
    if not isinstance(email, str):
        return False
    email = email.strip()
    if not email:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    if local.startswith(".") or local.endswith("."):
        return False
    for ch in local:
        if not (ch.isalnum() or ch in "._-+"):
            return False
    domain_parts = domain.split(".")
    for part in domain_parts:
        if not part:
            return False
    return True
''',
        "module_b": '''def format_email(email):
    email = email.strip().lower()
    parts = email.split("@")
    if len(parts) != 2:
        return email
    local, domain = parts
    if "+" in local:
        local = local[:local.index("+")]
    local = local.replace("..", ".")
    result = local + "@" + domain
    return result

def format_email_display(email, name=""):
    clean = format_email(email)
    if name:
        return name + " <" + clean + ">"
    return clean

def extract_domain(email):
    parts = email.split("@")
    if len(parts) == 2:
        return parts[1].strip().lower()
    return ""
''',
    },
    "phone_validator_formatter": {
        "module_a": '''def validate_phone(phone):
    if not isinstance(phone, str):
        return False
    digits = ""
    for ch in phone:
        if ch.isdigit():
            digits += ch
    if len(digits) < 10 or len(digits) > 15:
        return False
    if phone.startswith("+"):
        if len(digits) < 11:
            return False
    return True

def extract_country_code(phone):
    digits = "".join(c for c in phone if c.isdigit())
    if phone.startswith("+") and len(digits) >= 11:
        return digits[0]
    return ""

def is_toll_free(phone):
    digits = "".join(c for c in phone if c.isdigit())
    prefix = digits[:3] if len(digits) >= 10 else ""
    return prefix in ("800", "888", "877", "866", "855", "844")
''',
        "module_b": '''def format_phone(phone):
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return "({}) {}-{}".format(digits[:3], digits[3:6], digits[6:])
    elif len(digits) == 11:
        return "+{} ({}) {}-{}".format(
            digits[0], digits[1:4], digits[4:7], digits[7:])
    return phone

def format_phone_international(phone, country="1"):
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = country + digits
    return "+" + digits

def format_phone_e164(phone):
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits

def mask_phone(phone):
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) >= 4:
        return "***-***-" + digits[-4:]
    return "****"
''',
    },
    "url_parser_builder": {
        "module_a": '''def parse_url(url):
    result = {"scheme": "", "host": "", "port": "", "path": "", "query": ""}
    if "://" in url:
        result["scheme"], rest = url.split("://", 1)
    else:
        rest = url
    if "?" in rest:
        rest, result["query"] = rest.split("?", 1)
    if "/" in rest:
        idx = rest.index("/")
        host_part = rest[:idx]
        result["path"] = rest[idx:]
    else:
        host_part = rest
    if ":" in host_part:
        result["host"], result["port"] = host_part.rsplit(":", 1)
    else:
        result["host"] = host_part
    return result

def parse_query_string(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
        else:
            params[pair] = ""
    return params
''',
        "module_b": '''def build_url(scheme, host, port="", path="", query=""):
    url = ""
    if scheme:
        url = scheme + "://"
    url += host
    if port:
        url += ":" + str(port)
    if path:
        if not path.startswith("/"):
            path = "/" + path
        url += path
    if query:
        url += "?" + query
    return url

def build_query_string(params):
    if not params:
        return ""
    parts = []
    for key in sorted(params.keys()):
        value = params[key]
        parts.append(str(key) + "=" + str(value))
    return "&".join(parts)

def normalize_url(url):
    parsed = {"scheme": "", "host": "", "path": ""}
    if "://" in url:
        parsed["scheme"], rest = url.split("://", 1)
    else:
        rest = url
    if "/" in rest:
        parsed["host"] = rest[:rest.index("/")]
        parsed["path"] = rest[rest.index("/"):]
    else:
        parsed["host"] = rest
    parsed["host"] = parsed["host"].lower()
    return build_url(parsed["scheme"], parsed["host"], path=parsed["path"])
''',
    },
    "ipv4_validator_formatter": {
        "module_a": '''def validate_ipv4(addr):
    if not isinstance(addr, str):
        return False
    parts = addr.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part:
            return False
        if not part.isdigit():
            return False
        num = int(part)
        if num < 0 or num > 255:
            return False
        if len(part) > 1 and part.startswith("0"):
            return False
    return True

def is_private_ip(addr):
    parts = addr.split(".")
    if len(parts) != 4:
        return False
    a, b = int(parts[0]), int(parts[1])
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    return False
''',
        "module_b": '''def format_ipv4(addr):
    parts = addr.strip().split(".")
    formatted = []
    for part in parts:
        part = part.strip()
        if part.isdigit():
            formatted.append(str(int(part)))
        else:
            formatted.append(part)
    return ".".join(formatted)

def ipv4_to_int(addr):
    parts = addr.split(".")
    result = 0
    for part in parts:
        result = result * 256 + int(part)
    return result

def int_to_ipv4(num):
    parts = []
    for i in range(4):
        parts.append(str(num % 256))
        num = num // 256
    parts.reverse()
    return ".".join(parts)

def ipv4_in_subnet(addr, subnet):
    if "/" not in subnet:
        return addr == subnet
    network, bits = subnet.split("/")
    bits = int(bits)
    mask = ((1 << 32) - 1) ^ ((1 << (32 - bits)) - 1)
    return (ipv4_to_int(addr) & mask) == (ipv4_to_int(network) & mask)
''',
    },
    "credit_card_validator_masker": {
        "module_a": '''def validate_credit_card(number):
    digits = "".join(c for c in str(number) if c.isdigit())
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0

def detect_card_type(number):
    digits = "".join(c for c in str(number) if c.isdigit())
    if digits.startswith("4"):
        return "visa"
    if digits[:2] in ("51","52","53","54","55"):
        return "mastercard"
    if digits[:2] in ("34", "37"):
        return "amex"
    if digits.startswith("6011"):
        return "discover"
    return "unknown"
''',
        "module_b": '''def mask_card_number(number):
    digits = "".join(c for c in str(number) if c.isdigit())
    if len(digits) < 4:
        return "****"
    masked = "*" * (len(digits) - 4) + digits[-4:]
    groups = []
    for i in range(0, len(masked), 4):
        groups.append(masked[i:i+4])
    return " ".join(groups)

def format_card_number(number):
    digits = "".join(c for c in str(number) if c.isdigit())
    groups = []
    for i in range(0, len(digits), 4):
        groups.append(digits[i:i+4])
    return " ".join(groups)

def format_expiry(month, year):
    m = str(month).zfill(2)
    y = str(year)
    if len(y) == 4:
        y = y[2:]
    return m + "/" + y

def card_summary(number):
    digits = "".join(c for c in str(number) if c.isdigit())
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return "ending in " + last4
''',
    },
    "date_parser_formatter": {
        "module_a": '''def parse_date(text):
    separators = ["-", "/", "."]
    sep = None
    for s in separators:
        if s in text:
            sep = s
            break
    if sep is None:
        return None
    parts = text.split(sep)
    if len(parts) != 3:
        return None
    nums = []
    for p in parts:
        if not p.isdigit():
            return None
        nums.append(int(p))
    if nums[0] > 31:
        year, month, day = nums
    elif nums[2] > 31:
        month, day, year = nums
    else:
        day, month, year = nums
    if year < 100:
        year += 2000
    return {"year": year, "month": month, "day": day}

def is_leap_year(year):
    if year % 400 == 0:
        return True
    if year % 100 == 0:
        return False
    return year % 4 == 0
''',
        "module_b": '''def format_date_iso(year, month, day):
    return "{:04d}-{:02d}-{:02d}".format(year, month, day)

def format_date_us(year, month, day):
    return "{:02d}/{:02d}/{:04d}".format(month, day, year)

def format_date_eu(year, month, day):
    return "{:02d}.{:02d}.{:04d}".format(day, month, year)

def format_date_long(year, month, day):
    months = [
        "January", "February", "March", "April",
        "May", "June", "July", "August",
        "September", "October", "November", "December"
    ]
    if 1 <= month <= 12:
        month_name = months[month - 1]
    else:
        month_name = "Unknown"
    return "{} {}, {}".format(month_name, day, year)

def day_of_year(year, month, day):
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[1] = 29
    total = 0
    for i in range(month - 1):
        total += days_in_month[i]
    total += day
    return total
''',
    },
    "color_validator_converter": {
        "module_a": '''def validate_hex_color(color):
    if not isinstance(color, str):
        return False
    color = color.strip()
    if not color.startswith("#"):
        return False
    hex_part = color[1:]
    if len(hex_part) not in (3, 6):
        return False
    valid_chars = "0123456789abcdefABCDEF"
    for ch in hex_part:
        if ch not in valid_chars:
            return False
    return True

def validate_rgb(r, g, b):
    for val in (r, g, b):
        if not isinstance(val, (int, float)):
            return False
        if val < 0 or val > 255:
            return False
    return True

def color_distance(c1, c2):
    dr = c1[0] - c2[0]
    dg = c1[1] - c2[1]
    db = c1[2] - c2[2]
    return (dr * dr + dg * dg + db * db) ** 0.5
''',
        "module_b": '''def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c + c for c in hex_color)
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b)

def rgb_to_hex(r, g, b):
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

def rgb_to_hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        h = s = 0.0
    else:
        d = mx - mn
        s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r:
            h = (g - b) / d + (6.0 if g < b else 0.0)
        elif mx == g:
            h = (b - r) / d + 2.0
        else:
            h = (r - g) / d + 4.0
        h /= 6.0
    return (round(h * 360), round(s * 100), round(l * 100))
''',
    },
    "zip_code_validator_formatter": {
        "module_a": '''def validate_us_zip(zipcode):
    if not isinstance(zipcode, str):
        return False
    zipcode = zipcode.strip()
    if len(zipcode) == 5:
        return zipcode.isdigit()
    if len(zipcode) == 10 and zipcode[5] == "-":
        return zipcode[:5].isdigit() and zipcode[6:].isdigit()
    return False

def validate_uk_postcode(code):
    code = code.strip().upper().replace(" ", "")
    if len(code) < 5 or len(code) > 7:
        return False
    inward = code[-3:]
    if not inward[0].isdigit():
        return False
    if not inward[1].isalpha() or not inward[2].isalpha():
        return False
    outward = code[:-3]
    if not outward[0].isalpha():
        return False
    return True

def zip_to_region(zipcode):
    if not zipcode or not zipcode[:1].isdigit():
        return "unknown"
    first = int(zipcode[0])
    regions = {0: "northeast", 1: "northeast", 2: "mid_atlantic",
               3: "southeast", 4: "midwest", 5: "midwest",
               6: "south_central", 7: "south_central",
               8: "mountain", 9: "pacific"}
    return regions.get(first, "unknown")
''',
        "module_b": '''def format_us_zip(zipcode):
    digits = "".join(c for c in zipcode if c.isdigit())
    if len(digits) == 9:
        return digits[:5] + "-" + digits[5:]
    if len(digits) == 5:
        return digits
    return zipcode.strip()

def format_uk_postcode(code):
    code = code.strip().upper().replace(" ", "")
    if len(code) >= 4:
        return code[:-3] + " " + code[-3:]
    return code

def format_canadian_postal(code):
    code = code.strip().upper().replace(" ", "")
    if len(code) == 6:
        return code[:3] + " " + code[3:]
    return code

def normalize_postal_code(code, country="US"):
    code = code.strip()
    if country == "US":
        return format_us_zip(code)
    elif country == "UK":
        return format_uk_postcode(code)
    elif country == "CA":
        return format_canadian_postal(code)
    return code
''',
    },
    "isbn_validator_formatter": {
        "module_a": '''def validate_isbn10(isbn):
    isbn = isbn.replace("-", "").replace(" ", "")
    if len(isbn) != 10:
        return False
    total = 0
    for i in range(9):
        if not isbn[i].isdigit():
            return False
        total += int(isbn[i]) * (10 - i)
    last = isbn[9]
    if last == "X" or last == "x":
        total += 10
    elif last.isdigit():
        total += int(last)
    else:
        return False
    return total % 11 == 0

def validate_isbn13(isbn):
    isbn = isbn.replace("-", "").replace(" ", "")
    if len(isbn) != 13:
        return False
    total = 0
    for i, ch in enumerate(isbn):
        if not ch.isdigit():
            return False
        weight = 1 if i % 2 == 0 else 3
        total += int(ch) * weight
    return total % 10 == 0
''',
        "module_b": '''def format_isbn10(isbn):
    isbn = isbn.replace("-", "").replace(" ", "")
    if len(isbn) != 10:
        return isbn
    return isbn[0] + "-" + isbn[1:4] + "-" + isbn[4:9] + "-" + isbn[9]

def format_isbn13(isbn):
    isbn = isbn.replace("-", "").replace(" ", "")
    if len(isbn) != 13:
        return isbn
    return isbn[:3] + "-" + isbn[3] + "-" + isbn[4:7] + "-" + isbn[7:12] + "-" + isbn[12]

def isbn10_to_isbn13(isbn10):
    isbn10 = isbn10.replace("-", "").replace(" ", "")
    if len(isbn10) != 10:
        return isbn10
    base = "978" + isbn10[:9]
    total = 0
    for i, ch in enumerate(base):
        weight = 1 if i % 2 == 0 else 3
        total += int(ch) * weight
    check = (10 - (total % 10)) % 10
    return base + str(check)

def isbn_display(isbn):
    clean = isbn.replace("-", "").replace(" ", "")
    if len(clean) == 10:
        return "ISBN-10: " + format_isbn10(clean)
    elif len(clean) == 13:
        return "ISBN-13: " + format_isbn13(clean)
    return "ISBN: " + isbn
''',
    },
    "password_strength_generator": {
        "module_a": '''def check_password_strength(password):
    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in "!@#$%^&*()-_=+[]{}|;:,.<>?" for c in password)
    if has_upper:
        score += 1
    if has_lower:
        score += 1
    if has_digit:
        score += 1
    if has_special:
        score += 1
    if score <= 2:
        return "weak"
    if score <= 4:
        return "medium"
    return "strong"

def has_common_patterns(password):
    pw = password.lower()
    common = ["password", "123456", "qwerty", "abc123", "letmein"]
    for pattern in common:
        if pattern in pw:
            return True
    for i in range(len(pw) - 2):
        if pw[i] == pw[i+1] == pw[i+2]:
            return True
    return False
''',
        "module_b": '''import random as _pwd_random

def generate_password(length=16, uppercase=True, digits=True, special=True):
    chars = "abcdefghijklmnopqrstuvwxyz"
    if uppercase:
        chars += "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if digits:
        chars += "0123456789"
    if special:
        chars += "!@#$%^&*-_=+"
    result = []
    for i in range(length):
        result.append(_pwd_random.choice(chars))
    return "".join(result)

def generate_passphrase(word_count=4, separator="-"):
    words = [
        "apple", "bridge", "castle", "delta", "eagle",
        "forest", "garden", "harbor", "island", "jungle",
        "kettle", "lemon", "marble", "noble", "ocean",
        "palace", "quartz", "river", "silver", "tower"
    ]
    chosen = []
    for i in range(word_count):
        chosen.append(_pwd_random.choice(words))
    return separator.join(chosen)

def mask_password(password):
    if len(password) <= 2:
        return "*" * len(password)
    return password[0] + "*" * (len(password) - 2) + password[-1]
''',
    },
    "base64_encoder_decoder": {
        "module_a": '''_B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

def base64_encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    result = []
    padding = 0
    i = 0
    while i < len(data):
        chunk = data[i:i+3]
        i += 3
        b = 0
        for byte in chunk:
            b = (b << 8) | byte
        missing = 3 - len(chunk)
        b <<= (missing * 8)
        padding += missing
        for j in range(3, -1, -1):
            idx = (b >> (j * 6)) & 0x3F
            result.append(_B64_CHARS[idx])
    if padding:
        result[-padding:] = ["="] * padding
    return "".join(result)

def base64_encode_urlsafe(data):
    encoded = base64_encode(data)
    return encoded.replace("+", "-").replace("/", "_").rstrip("=")
''',
        "module_b": '''_B64_DECODE_MAP = {}
_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
for _i, _c in enumerate(_B64_ALPHABET):
    _B64_DECODE_MAP[_c] = _i

def base64_decode(encoded):
    encoded = encoded.rstrip("=")
    padding = (4 - len(encoded) % 4) % 4
    encoded += "A" * padding
    result = bytearray()
    for i in range(0, len(encoded), 4):
        chunk = encoded[i:i+4]
        b = 0
        for c in chunk:
            b = (b << 6) | _B64_DECODE_MAP.get(c, 0)
        result.append((b >> 16) & 0xFF)
        result.append((b >> 8) & 0xFF)
        result.append(b & 0xFF)
    if padding:
        result = result[:-padding]
    return bytes(result)

def base64_decode_to_string(encoded):
    raw = base64_decode(encoded)
    return raw.decode("utf-8", errors="replace")

def base64_decode_urlsafe(encoded):
    encoded = encoded.replace("-", "+").replace("_", "/")
    padding = (4 - len(encoded) % 4) % 4
    encoded += "=" * padding
    return base64_decode(encoded)
''',
    },
    "hex_encoder_decoder": {
        "module_a": '''_HEX_CHARS = "0123456789abcdef"

def hex_encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    result = []
    for byte in data:
        result.append(_HEX_CHARS[byte >> 4])
        result.append(_HEX_CHARS[byte & 0x0F])
    return "".join(result)

def hex_encode_upper(data):
    return hex_encode(data).upper()

def hex_dump(data, bytes_per_line=16):
    if isinstance(data, str):
        data = data.encode("utf-8")
    lines = []
    for offset in range(0, len(data), bytes_per_line):
        chunk = data[offset:offset + bytes_per_line]
        hex_part = " ".join("{:02x}".format(b) for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("{:08x}  {:<48s}  {}".format(offset, hex_part, ascii_part))
    return "\\n".join(lines)
''',
        "module_b": '''def hex_decode(hex_str):
    hex_str = hex_str.strip().replace(" ", "").replace("\\n", "")
    if hex_str.startswith("0x") or hex_str.startswith("0X"):
        hex_str = hex_str[2:]
    if len(hex_str) % 2 != 0:
        hex_str = "0" + hex_str
    result = bytearray()
    for i in range(0, len(hex_str), 2):
        byte_str = hex_str[i:i+2]
        result.append(int(byte_str, 16))
    return bytes(result)

def hex_decode_to_string(hex_str):
    raw = hex_decode(hex_str)
    return raw.decode("utf-8", errors="replace")

def is_valid_hex(hex_str):
    hex_str = hex_str.strip()
    if hex_str.startswith("0x") or hex_str.startswith("0X"):
        hex_str = hex_str[2:]
    if not hex_str:
        return False
    valid = "0123456789abcdefABCDEF"
    for ch in hex_str:
        if ch not in valid:
            return False
    return True

def hex_to_int(hex_str):
    hex_str = hex_str.strip()
    if hex_str.startswith("0x") or hex_str.startswith("0X"):
        hex_str = hex_str[2:]
    return int(hex_str, 16)
''',
    },
    "csv_writer_reader": {
        "module_a": '''def csv_write_row(fields, delimiter=","):
    parts = []
    for field in fields:
        s = str(field)
        needs_quote = False
        if delimiter in s or '"' in s or "\\n" in s:
            needs_quote = True
        if needs_quote:
            s = '"' + s.replace('"', '""') + '"'
        parts.append(s)
    return delimiter.join(parts)

def csv_write_rows(rows, delimiter=","):
    lines = []
    for row in rows:
        lines.append(csv_write_row(row, delimiter))
    return "\\n".join(lines)

def csv_write_dict_rows(rows, fieldnames, delimiter=","):
    lines = [csv_write_row(fieldnames, delimiter)]
    for row in rows:
        values = [row.get(f, "") for f in fieldnames]
        lines.append(csv_write_row(values, delimiter))
    return "\\n".join(lines)
''',
        "module_b": '''def csv_read_row(line, delimiter=","):
    fields = []
    current = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_quotes:
            if ch == '"' and i + 1 < len(line) and line[i+1] == '"':
                current.append('"')
                i += 2
                continue
            elif ch == '"':
                in_quotes = False
            else:
                current.append(ch)
        else:
            if ch == '"':
                in_quotes = True
            elif ch == delimiter:
                fields.append("".join(current))
                current = []
            else:
                current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields

def csv_read_rows(text, delimiter=","):
    rows = []
    for line in text.strip().split("\\n"):
        if line.strip():
            rows.append(csv_read_row(line, delimiter))
    return rows
''',
    },
    "keyvalue_serializer_deserializer": {
        "module_a": '''def serialize_kv(data, separator="=", line_sep="\\n"):
    lines = []
    for key in sorted(data.keys()):
        value = data[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        else:
            value = str(value)
        key_str = str(key).strip()
        lines.append(key_str + separator + value)
    return line_sep.join(lines)

def serialize_nested_kv(data, prefix=""):
    lines = []
    for key in sorted(data.keys()):
        full_key = prefix + "." + key if prefix else key
        value = data[key]
        if isinstance(value, dict):
            lines.append(serialize_nested_kv(value, full_key))
        else:
            lines.append(full_key + "=" + str(value))
    return "\\n".join(lines)
''',
        "module_b": '''def deserialize_kv(text, separator="="):
    result = {}
    for line in text.strip().split("\\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if separator not in line:
            continue
        key, value = line.split(separator, 1)
        key = key.strip()
        value = value.strip()
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        else:
            try:
                value = float(value)
            except ValueError:
                pass
        result[key] = value
    return result

def deserialize_nested_kv(text, separator="="):
    flat = deserialize_kv(text, separator)
    result = {}
    for key, value in flat.items():
        parts = key.split(".")
        current = result
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
    return result
''',
    },
    "rle_encoder_decoder": {
        "module_a": '''def rle_encode(data):
    if not data:
        return ""
    result = []
    current = data[0]
    count = 1
    for i in range(1, len(data)):
        if data[i] == current:
            count += 1
        else:
            if count > 1:
                result.append(str(count) + current)
            else:
                result.append(current)
            current = data[i]
            count = 1
    if count > 1:
        result.append(str(count) + current)
    else:
        result.append(current)
    return "".join(result)

def rle_encode_bytes(data):
    if not data:
        return b""
    result = bytearray()
    current = data[0]
    count = 1
    for i in range(1, len(data)):
        if data[i] == current and count < 255:
            count += 1
        else:
            result.append(count)
            result.append(current)
            current = data[i]
            count = 1
    result.append(count)
    result.append(current)
    return bytes(result)
''',
        "module_b": '''def rle_decode(encoded):
    result = []
    i = 0
    while i < len(encoded):
        num_str = ""
        while i < len(encoded) and encoded[i].isdigit():
            num_str += encoded[i]
            i += 1
        if i < len(encoded):
            ch = encoded[i]
            i += 1
            count = int(num_str) if num_str else 1
            result.append(ch * count)
    return "".join(result)

def rle_decode_bytes(data):
    result = bytearray()
    i = 0
    while i + 1 < len(data):
        count = data[i]
        value = data[i + 1]
        for j in range(count):
            result.append(value)
        i += 2
    return bytes(result)

def rle_compression_ratio(original, encoded):
    if not original:
        return 0.0
    orig_len = len(original)
    enc_len = len(encoded)
    if orig_len == 0:
        return 0.0
    return 1.0 - (enc_len / orig_len)
''',
    },
    "morse_encoder_decoder": {
        "module_a": '''_MORSE_TABLE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....",
    "7": "--...", "8": "---..", "9": "----.",
}

def morse_encode(text):
    result = []
    for ch in text.upper():
        if ch == " ":
            result.append("/")
        elif ch in _MORSE_TABLE:
            result.append(_MORSE_TABLE[ch])
    return " ".join(result)

def morse_encode_word(word):
    codes = []
    for ch in word.upper():
        if ch in _MORSE_TABLE:
            codes.append(_MORSE_TABLE[ch])
    return " ".join(codes)
''',
        "module_b": '''_MORSE_DECODE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6",
    "--...": "7", "---..": "8", "----.": "9",
}

def morse_decode(encoded):
    words = encoded.split(" / ")
    decoded_words = []
    for word in words:
        letters = word.strip().split(" ")
        decoded = ""
        for code in letters:
            if code in _MORSE_DECODE:
                decoded += _MORSE_DECODE[code]
        decoded_words.append(decoded)
    return " ".join(decoded_words)

def morse_decode_strict(encoded):
    result = []
    for code in encoded.split(" "):
        if code == "/":
            result.append(" ")
        elif code in _MORSE_DECODE:
            result.append(_MORSE_DECODE[code])
    return "".join(result)
''',
    },
    "roman_encoder_decoder": {
        "module_a": '''def int_to_roman(num):
    if num <= 0 or num > 3999:
        return ""
    values = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    symbols = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    result = []
    for i, val in enumerate(values):
        while num >= val:
            result.append(symbols[i])
            num -= val
    return "".join(result)

def int_to_roman_lower(num):
    return int_to_roman(num).lower()

def format_roman_list(items):
    result = []
    for i, item in enumerate(items, 1):
        numeral = int_to_roman(i)
        result.append(numeral + ". " + str(item))
    return result

def is_valid_roman_range(num):
    return isinstance(num, int) and 1 <= num <= 3999
''',
        "module_b": '''def roman_to_int(roman):
    roman = roman.upper().strip()
    values = {"I": 1, "V": 5, "X": 10, "L": 50,
              "C": 100, "D": 500, "M": 1000}
    result = 0
    prev = 0
    for ch in reversed(roman):
        curr = values.get(ch, 0)
        if curr < prev:
            result -= curr
        else:
            result += curr
        prev = curr
    return result

def validate_roman(roman):
    roman = roman.upper().strip()
    valid_chars = set("IVXLCDM")
    for ch in roman:
        if ch not in valid_chars:
            return False
    value = roman_to_int(roman)
    return value > 0

def roman_compare(a, b):
    val_a = roman_to_int(a)
    val_b = roman_to_int(b)
    if val_a < val_b:
        return -1
    if val_a > val_b:
        return 1
    return 0

def roman_add(a, b):
    from_a = roman_to_int(a)
    from_b = roman_to_int(b)
    total = from_a + from_b
    values = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    symbols = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    result = []
    for i, val in enumerate(values):
        while total >= val:
            result.append(symbols[i])
            total -= val
    return "".join(result)
''',
    },
    "caesar_encoder_decoder": {
        "module_a": '''def caesar_encrypt(text, shift=3):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            shifted = (ord(ch) - base + shift) % 26
            result.append(chr(base + shifted))
        else:
            result.append(ch)
    return "".join(result)

def caesar_encrypt_with_key(text, key):
    shift = sum(ord(c) for c in key) % 26
    return caesar_encrypt(text, shift)

def vigenere_encrypt(text, key):
    result = []
    key_idx = 0
    key = key.upper()
    for ch in text:
        if ch.isalpha():
            shift = ord(key[key_idx % len(key)]) - ord("A")
            base = ord("A") if ch.isupper() else ord("a")
            shifted = (ord(ch) - base + shift) % 26
            result.append(chr(base + shifted))
            key_idx += 1
        else:
            result.append(ch)
    return "".join(result)
''',
        "module_b": '''def caesar_decrypt(text, shift=3):
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            shifted = (ord(ch) - base - shift) % 26
            result.append(chr(base + shifted))
        else:
            result.append(ch)
    return "".join(result)

def caesar_brute_force(text):
    results = {}
    for shift in range(26):
        decrypted = caesar_decrypt(text, shift)
        results[shift] = decrypted
    return results

def vigenere_decrypt(text, key):
    result = []
    key_idx = 0
    key = key.upper()
    for ch in text:
        if ch.isalpha():
            shift = ord(key[key_idx % len(key)]) - ord("A")
            base = ord("A") if ch.isupper() else ord("a")
            shifted = (ord(ch) - base - shift) % 26
            result.append(chr(base + shifted))
            key_idx += 1
        else:
            result.append(ch)
    return "".join(result)
''',
    },
    "binary_packer_unpacker": {
        "module_a": '''def pack_uint8(value):
    return bytes([value & 0xFF])

def pack_uint16_be(value):
    return bytes([(value >> 8) & 0xFF, value & 0xFF])

def pack_uint32_be(value):
    return bytes([
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ])

def pack_string(s):
    encoded = s.encode("utf-8")
    length = len(encoded)
    header = pack_uint16_be(length)
    return header + encoded

def pack_message(msg_type, payload):
    type_bytes = pack_uint8(msg_type)
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    length = pack_uint32_be(len(payload_bytes))
    return type_bytes + length + payload_bytes
''',
        "module_b": '''def unpack_uint8(data, offset=0):
    return data[offset], offset + 1

def unpack_uint16_be(data, offset=0):
    value = (data[offset] << 8) | data[offset + 1]
    return value, offset + 2

def unpack_uint32_be(data, offset=0):
    value = (
        (data[offset] << 24) |
        (data[offset + 1] << 16) |
        (data[offset + 2] << 8) |
        data[offset + 3]
    )
    return value, offset + 4

def unpack_string(data, offset=0):
    length, offset = unpack_uint16_be(data, offset)
    s = data[offset:offset + length].decode("utf-8")
    return s, offset + length

def unpack_message(data, offset=0):
    msg_type, offset = unpack_uint8(data, offset)
    length, offset = unpack_uint32_be(data, offset)
    payload = data[offset:offset + length]
    return {"type": msg_type, "payload": payload}, offset + length
''',
    },
    "slug_generator_resolver": {
        "module_a": '''def generate_slug(text):
    text = text.lower().strip()
    result = []
    prev_dash = False
    for ch in text:
        if ch.isalnum():
            result.append(ch)
            prev_dash = False
        elif ch in " -_":
            if not prev_dash and result:
                result.append("-")
                prev_dash = True
    slug = "".join(result).rstrip("-")
    return slug

def generate_unique_slug(text, existing_slugs):
    base = generate_slug(text)
    if base not in existing_slugs:
        return base
    counter = 1
    while True:
        candidate = base + "-" + str(counter)
        if candidate not in existing_slugs:
            return candidate
        counter += 1

def truncate_slug(slug, max_len=50):
    if len(slug) <= max_len:
        return slug
    truncated = slug[:max_len]
    last_dash = truncated.rfind("-")
    if last_dash > max_len // 2:
        truncated = truncated[:last_dash]
    return truncated.rstrip("-")
''',
        "module_b": '''def resolve_slug(slug, slug_map):
    if slug in slug_map:
        return slug_map[slug]
    for key, value in slug_map.items():
        if key.startswith(slug):
            return value
    return None

def slug_to_title(slug):
    words = slug.split("-")
    titled = []
    for word in words:
        if word:
            titled.append(word[0].upper() + word[1:])
    return " ".join(titled)

def find_similar_slugs(slug, slug_list, max_distance=3):
    similar = []
    for candidate in slug_list:
        dist = _edit_distance(slug, candidate)
        if dist <= max_distance:
            similar.append((candidate, dist))
    similar.sort(key=lambda x: x[1])
    return similar

def _edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i-1] == b[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(dp[j], dp[j-1], prev)
            prev = temp
    return dp[n]
''',
    },
    "event_emitter_listener": {
        "module_a": '''class EventEmitter:
    def __init__(self):
        self._handlers = {}
        self._event_log = []

    def on(self, event, handler):
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)

    def off(self, event, handler=None):
        if event not in self._handlers:
            return
        if handler is None:
            del self._handlers[event]
        else:
            self._handlers[event] = [
                h for h in self._handlers[event] if h != handler
            ]

    def emit(self, event, *args, **kwargs):
        self._event_log.append(event)
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            handler(*args, **kwargs)

    def event_count(self):
        return len(self._event_log)

    def has_listeners(self, event):
        return event in self._handlers and len(self._handlers[event]) > 0
''',
        "module_b": '''class EventListener:
    def __init__(self):
        self.received = []
        self.counts = {}

    def handle(self, event_name, data=None):
        self.received.append({"event": event_name, "data": data})
        self.counts[event_name] = self.counts.get(event_name, 0) + 1

    def get_events(self, event_name=None):
        if event_name is None:
            return list(self.received)
        return [e for e in self.received if e["event"] == event_name]

    def get_count(self, event_name):
        return self.counts.get(event_name, 0)

    def clear(self):
        self.received = []
        self.counts = {}

    def last_event(self):
        if self.received:
            return self.received[-1]
        return None

    def has_received(self, event_name):
        return event_name in self.counts
''',
    },
    "queue_producer_consumer": {
        "module_a": '''class QueueProducer:
    def __init__(self, max_size=100):
        self._queue = []
        self._max_size = max_size
        self._produced_count = 0

    def produce(self, item):
        if len(self._queue) >= self._max_size:
            return False
        self._queue.append(item)
        self._produced_count += 1
        return True

    def produce_batch(self, items):
        added = 0
        for item in items:
            if self.produce(item):
                added += 1
            else:
                break
        return added

    def size(self):
        return len(self._queue)

    def is_full(self):
        return len(self._queue) >= self._max_size

    def get_queue(self):
        return list(self._queue)

    def total_produced(self):
        return self._produced_count
''',
        "module_b": '''class QueueConsumer:
    def __init__(self, queue_ref):
        self._queue = queue_ref
        self._consumed_count = 0
        self._last_item = None

    def consume(self):
        if not self._queue:
            return None
        item = self._queue.pop(0)
        self._consumed_count += 1
        self._last_item = item
        return item

    def consume_batch(self, count):
        items = []
        for i in range(count):
            item = self.consume()
            if item is None:
                break
            items.append(item)
        return items

    def peek(self):
        if self._queue:
            return self._queue[0]
        return None

    def is_empty(self):
        return len(self._queue) == 0

    def total_consumed(self):
        return self._consumed_count

    def last_consumed(self):
        return self._last_item
''',
    },
    "cache_store_retriever": {
        "module_a": '''class CacheStore:
    def __init__(self, max_entries=1000):
        self._data = {}
        self._max_entries = max_entries
        self._write_count = 0

    def put(self, key, value, ttl=None):
        if len(self._data) >= self._max_entries and key not in self._data:
            oldest_key = next(iter(self._data))
            del self._data[oldest_key]
        import time as _t
        self._data[key] = {
            "value": value,
            "created": _t.time(),
            "ttl": ttl,
        }
        self._write_count += 1

    def invalidate(self, key):
        if key in self._data:
            del self._data[key]
            return True
        return False

    def clear(self):
        self._data.clear()
        self._write_count = 0

    def keys(self):
        return list(self._data.keys())

    def size(self):
        return len(self._data)
''',
        "module_b": '''class CacheRetriever:
    def __init__(self, cache_data):
        self._data = cache_data
        self._hit_count = 0
        self._miss_count = 0

    def get(self, key):
        if key in self._data:
            entry = self._data[key]
            import time as _t
            if entry["ttl"] is not None:
                age = _t.time() - entry["created"]
                if age > entry["ttl"]:
                    del self._data[key]
                    self._miss_count += 1
                    return None
            self._hit_count += 1
            return entry["value"]
        self._miss_count += 1
        return None

    def exists(self, key):
        return key in self._data

    def hit_rate(self):
        total = self._hit_count + self._miss_count
        if total == 0:
            return 0.0
        return self._hit_count / total

    def stats(self):
        return {
            "hits": self._hit_count,
            "misses": self._miss_count,
            "hit_rate": self.hit_rate(),
        }
''',
    },
    "logger_parser": {
        "module_a": '''class SimpleLogger:
    LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

    def __init__(self, min_level="DEBUG"):
        self._min_level = self.LEVELS.get(min_level, 0)
        self._entries = []

    def _log(self, level, message):
        if self.LEVELS.get(level, 0) >= self._min_level:
            import time as _t
            entry = "{} [{}] {}".format(
                _t.strftime("%Y-%m-%d %H:%M:%S"), level, message)
            self._entries.append(entry)

    def debug(self, msg):
        self._log("DEBUG", msg)

    def info(self, msg):
        self._log("INFO", msg)

    def warning(self, msg):
        self._log("WARNING", msg)

    def error(self, msg):
        self._log("ERROR", msg)

    def critical(self, msg):
        self._log("CRITICAL", msg)

    def get_logs(self):
        return list(self._entries)

    def count(self):
        return len(self._entries)
''',
        "module_b": '''class LogParser:
    def __init__(self):
        self._parsed = []

    def parse(self, log_text):
        self._parsed = []
        for line in log_text.strip().split("\\n"):
            entry = self._parse_line(line)
            if entry:
                self._parsed.append(entry)
        return self._parsed

    def _parse_line(self, line):
        line = line.strip()
        if not line:
            return None
        bracket_start = line.find("[")
        bracket_end = line.find("]")
        if bracket_start < 0 or bracket_end < 0:
            return {"timestamp": "", "level": "UNKNOWN", "message": line}
        timestamp = line[:bracket_start].strip()
        level = line[bracket_start+1:bracket_end].strip()
        message = line[bracket_end+1:].strip()
        return {"timestamp": timestamp, "level": level, "message": message}

    def filter_by_level(self, level):
        return [e for e in self._parsed if e["level"] == level]

    def error_count(self):
        return len([e for e in self._parsed if e["level"] in ("ERROR", "CRITICAL")])

    def get_messages(self):
        return [e["message"] for e in self._parsed]
''',
    },
    "config_writer_reader": {
        "module_a": '''class ConfigWriter:
    def __init__(self):
        self._sections = {}

    def set(self, section, key, value):
        if section not in self._sections:
            self._sections[section] = {}
        self._sections[section][key] = value

    def remove(self, section, key=None):
        if section in self._sections:
            if key is None:
                del self._sections[section]
            elif key in self._sections[section]:
                del self._sections[section][key]

    def to_string(self):
        lines = []
        for section in sorted(self._sections.keys()):
            lines.append("[" + section + "]")
            for key in sorted(self._sections[section].keys()):
                value = self._sections[section][key]
                lines.append(key + " = " + str(value))
            lines.append("")
        return "\\n".join(lines)

    def sections(self):
        return list(self._sections.keys())

    def get_section(self, section):
        return dict(self._sections.get(section, {}))
''',
        "module_b": '''class ConfigReader:
    def __init__(self):
        self._data = {}

    def parse(self, text):
        self._data = {}
        current_section = "DEFAULT"
        for line in text.split("\\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                if current_section not in self._data:
                    self._data[current_section] = {}
            elif "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if current_section not in self._data:
                    self._data[current_section] = {}
                self._data[current_section][key] = value

    def get(self, section, key, default=None):
        return self._data.get(section, {}).get(key, default)

    def sections(self):
        return list(self._data.keys())

    def has_section(self, section):
        return section in self._data

    def items(self, section):
        return list(self._data.get(section, {}).items())
''',
    },
    "rate_limiter_requester": {
        "module_a": '''class RateLimiter:
    def __init__(self, max_requests, window_seconds):
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests = {}

    def allow(self, client_id):
        import time as _t
        now = _t.time()
        if client_id not in self._requests:
            self._requests[client_id] = []
        timestamps = self._requests[client_id]
        timestamps[:] = [t for t in timestamps if now - t < self._window]
        if len(timestamps) >= self._max_requests:
            return False
        timestamps.append(now)
        return True

    def remaining(self, client_id):
        import time as _t
        now = _t.time()
        timestamps = self._requests.get(client_id, [])
        active = [t for t in timestamps if now - t < self._window]
        return max(0, self._max_requests - len(active))

    def reset(self, client_id):
        if client_id in self._requests:
            self._requests[client_id] = []

    def reset_all(self):
        self._requests.clear()
''',
        "module_b": '''class Requester:
    def __init__(self):
        self._history = []
        self._success = 0
        self._rejected = 0

    def make_request(self, endpoint, data=None):
        self._history.append({
            "endpoint": endpoint,
            "data": data,
            "status": "pending",
        })
        return len(self._history) - 1

    def mark_success(self, request_id):
        if 0 <= request_id < len(self._history):
            self._history[request_id]["status"] = "success"
            self._success += 1

    def mark_rejected(self, request_id):
        if 0 <= request_id < len(self._history):
            self._history[request_id]["status"] = "rejected"
            self._rejected += 1

    def success_rate(self):
        total = self._success + self._rejected
        if total == 0:
            return 0.0
        return self._success / total

    def get_history(self):
        return list(self._history)

    def pending_count(self):
        return sum(1 for r in self._history if r["status"] == "pending")
''',
    },
    "token_generator_validator": {
        "module_a": '''import hashlib as _hashlib
import json as _json
import time as _time

def generate_token(payload, secret="default_secret"):
    header = {"alg": "HS256", "typ": "JWT"}
    payload["iat"] = int(_time.time())
    header_str = _json.dumps(header, sort_keys=True)
    payload_str = _json.dumps(payload, sort_keys=True)
    message = header_str + "." + payload_str
    signature = _hashlib.sha256(
        (message + secret).encode("utf-8")).hexdigest()
    return header_str + "." + payload_str + "." + signature

def generate_refresh_token(user_id, secret="refresh_secret"):
    payload = {"sub": user_id, "type": "refresh"}
    return generate_token(payload, secret)

def token_expires_at(token):
    parts = token.split(".")
    if len(parts) < 2:
        return 0
    try:
        payload = _json.loads(parts[1])
        return payload.get("exp", 0)
    except (ValueError, KeyError):
        return 0
''',
        "module_b": '''import hashlib as _hashlib
import json as _json
import time as _time

def validate_token(token, secret="default_secret"):
    parts = token.split(".")
    if len(parts) != 3:
        return {"valid": False, "error": "malformed token"}
    header_str, payload_str, signature = parts
    message = header_str + "." + payload_str
    expected = _hashlib.sha256(
        (message + secret).encode("utf-8")).hexdigest()
    if signature != expected:
        return {"valid": False, "error": "invalid signature"}
    try:
        payload = _json.loads(payload_str)
    except ValueError:
        return {"valid": False, "error": "invalid payload"}
    if "exp" in payload and payload["exp"] < _time.time():
        return {"valid": False, "error": "token expired"}
    return {"valid": True, "payload": payload}

def extract_payload(token):
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        return _json.loads(parts[1])
    except ValueError:
        return {}
''',
    },
    "service_registry_resolver": {
        "module_a": '''class ServiceRegistry:
    def __init__(self):
        self._services = {}
        self._metadata = {}

    def register(self, name, endpoint, metadata=None):
        if name not in self._services:
            self._services[name] = []
        self._services[name].append(endpoint)
        if metadata:
            self._metadata[name + ":" + endpoint] = metadata

    def deregister(self, name, endpoint=None):
        if name not in self._services:
            return False
        if endpoint is None:
            del self._services[name]
        else:
            self._services[name] = [
                e for e in self._services[name] if e != endpoint
            ]
        return True

    def list_services(self):
        return list(self._services.keys())

    def get_endpoints(self, name):
        return list(self._services.get(name, []))

    def service_count(self):
        return len(self._services)

    def total_endpoints(self):
        return sum(len(eps) for eps in self._services.values())
''',
        "module_b": '''class ServiceResolver:
    def __init__(self, registry_data):
        self._data = registry_data
        self._index = {}

    def resolve(self, name):
        endpoints = self._data.get(name, [])
        if not endpoints:
            return None
        idx = self._index.get(name, 0)
        endpoint = endpoints[idx % len(endpoints)]
        self._index[name] = idx + 1
        return endpoint

    def resolve_all(self, name):
        return list(self._data.get(name, []))

    def find_by_prefix(self, prefix):
        matches = []
        for name in self._data:
            if name.startswith(prefix):
                matches.append(name)
        return matches

    def health_check(self, name):
        endpoints = self._data.get(name, [])
        return {
            "service": name,
            "endpoints": len(endpoints),
            "available": len(endpoints) > 0,
        }

    def dependency_map(self, services):
        result = {}
        for service in services:
            result[service] = {
                "endpoints": len(self._data.get(service, [])),
                "resolved": service in self._data,
            }
        return result
''',
    },
    "retry_policy_executor": {
        "module_a": '''class RetryPolicy:
    def __init__(self, max_retries=3, base_delay=1.0, backoff_factor=2.0):
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._backoff_factor = backoff_factor
        self._retryable_errors = set()

    def add_retryable_error(self, error_type):
        self._retryable_errors.add(error_type)

    def should_retry(self, attempt, error_type=None):
        if attempt >= self._max_retries:
            return False
        if self._retryable_errors and error_type not in self._retryable_errors:
            return False
        return True

    def get_delay(self, attempt):
        delay = self._base_delay * (self._backoff_factor ** attempt)
        return min(delay, 60.0)

    def max_retries(self):
        return self._max_retries

    def total_max_delay(self):
        total = 0.0
        for i in range(self._max_retries):
            total += self.get_delay(i)
        return total

    def describe(self):
        return "RetryPolicy(max={}, base={}s, backoff={}x)".format(
            self._max_retries, self._base_delay, self._backoff_factor)
''',
        "module_b": '''class RetryExecutor:
    def __init__(self, policy):
        self._policy = policy
        self._attempts = []

    def execute(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(self._policy.max_retries() + 1):
            try:
                result = func(*args, **kwargs)
                self._attempts.append({
                    "attempt": attempt,
                    "success": True,
                })
                return result
            except Exception as e:
                last_error = e
                error_type = type(e).__name__
                self._attempts.append({
                    "attempt": attempt,
                    "success": False,
                    "error": error_type,
                })
                if not self._policy.should_retry(attempt, error_type):
                    break
        raise last_error

    def get_attempts(self):
        return list(self._attempts)

    def success_count(self):
        return sum(1 for a in self._attempts if a["success"])

    def failure_count(self):
        return sum(1 for a in self._attempts if not a["success"])

    def clear_history(self):
        self._attempts = []
''',
    },
    "circuit_breaker_client": {
        "module_a": '''class CircuitBreaker:
    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self._state = self.STATE_CLOSED
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time = 0

    def record_success(self):
        self._failure_count = 0
        self._state = self.STATE_CLOSED

    def record_failure(self):
        import time as _t
        self._failure_count += 1
        self._last_failure_time = _t.time()
        if self._failure_count >= self._failure_threshold:
            self._state = self.STATE_OPEN

    def allow_request(self):
        if self._state == self.STATE_CLOSED:
            return True
        if self._state == self.STATE_OPEN:
            import time as _t
            if _t.time() - self._last_failure_time > self._recovery_timeout:
                self._state = self.STATE_HALF_OPEN
                return True
            return False
        return True

    def get_state(self):
        return self._state

    def failure_count(self):
        return self._failure_count
''',
        "module_b": '''class CircuitBreakerClient:
    def __init__(self, breaker):
        self._breaker = breaker
        self._request_log = []

    def call(self, func, *args, **kwargs):
        if not self._breaker.allow_request():
            self._request_log.append({"status": "circuit_open"})
            return {"error": "circuit open", "success": False}
        try:
            result = func(*args, **kwargs)
            self._breaker.record_success()
            self._request_log.append({"status": "success"})
            return {"data": result, "success": True}
        except Exception as e:
            self._breaker.record_failure()
            self._request_log.append({
                "status": "failure",
                "error": str(e),
            })
            return {"error": str(e), "success": False}

    def get_log(self):
        return list(self._request_log)

    def success_count(self):
        return sum(1 for r in self._request_log if r["status"] == "success")

    def failure_count(self):
        return sum(1 for r in self._request_log if r["status"] == "failure")

    def circuit_state(self):
        return self._breaker.get_state()
''',
    },
    "inventory_tracker_reporter": {
        "module_a": '''class InventoryTracker:
    def __init__(self):
        self._items = {}
        self._history = []

    def add_item(self, sku, name, quantity, price):
        self._items[sku] = {
            "name": name, "quantity": quantity, "price": price
        }
        self._history.append(("add", sku, quantity))

    def remove_item(self, sku):
        if sku in self._items:
            del self._items[sku]
            self._history.append(("remove", sku, 0))

    def update_quantity(self, sku, delta):
        if sku in self._items:
            self._items[sku]["quantity"] += delta
            self._history.append(("update", sku, delta))
            return self._items[sku]["quantity"]
        return -1

    def get_item(self, sku):
        return self._items.get(sku)

    def low_stock(self, threshold=10):
        return [sku for sku, item in self._items.items()
                if item["quantity"] < threshold]

    def total_value(self):
        total = 0.0
        for item in self._items.values():
            total += item["quantity"] * item["price"]
        return round(total, 2)

    def item_count(self):
        return len(self._items)
''',
        "module_b": '''class InventoryReporter:
    def __init__(self, inventory_data):
        self._data = inventory_data

    def summary(self):
        total_items = len(self._data)
        total_units = sum(v["quantity"] for v in self._data.values())
        total_value = sum(
            v["quantity"] * v["price"] for v in self._data.values())
        return {
            "total_items": total_items,
            "total_units": total_units,
            "total_value": round(total_value, 2),
        }

    def top_by_value(self, n=5):
        items = []
        for sku, data in self._data.items():
            value = data["quantity"] * data["price"]
            items.append((sku, data["name"], round(value, 2)))
        items.sort(key=lambda x: x[2], reverse=True)
        return items[:n]

    def category_breakdown(self):
        categories = {}
        for sku, data in self._data.items():
            cat = sku.split("-")[0] if "-" in sku else "uncategorized"
            if cat not in categories:
                categories[cat] = {"count": 0, "value": 0.0}
            categories[cat]["count"] += 1
            categories[cat]["value"] += data["quantity"] * data["price"]
        return categories

    def format_report(self):
        lines = ["Inventory Report", "=" * 40]
        for sku, data in sorted(self._data.items()):
            lines.append("{}: {} x ${:.2f} = ${:.2f}".format(
                sku, data["quantity"], data["price"],
                data["quantity"] * data["price"]))
        return "\\n".join(lines)
''',
    },
    "price_calculator_formatter": {
        "module_a": '''def calculate_price(base_price, quantity, discount_pct=0):
    if quantity <= 0:
        return 0.0
    subtotal = base_price * quantity
    discount = subtotal * (discount_pct / 100.0)
    return round(subtotal - discount, 2)

def calculate_bulk_discount(base_price, quantity):
    if quantity >= 100:
        discount = 0.20
    elif quantity >= 50:
        discount = 0.15
    elif quantity >= 20:
        discount = 0.10
    elif quantity >= 10:
        discount = 0.05
    else:
        discount = 0.0
    return round(base_price * quantity * (1 - discount), 2)

def calculate_tax(amount, tax_rate=0.08):
    return round(amount * tax_rate, 2)

def calculate_total(items):
    subtotal = 0.0
    for item in items:
        price = item.get("price", 0)
        qty = item.get("quantity", 1)
        subtotal += price * qty
    return round(subtotal, 2)

def apply_coupon(total, coupon_type, coupon_value):
    if coupon_type == "percent":
        return round(total * (1 - coupon_value / 100.0), 2)
    elif coupon_type == "fixed":
        return round(max(0, total - coupon_value), 2)
    return total
''',
        "module_b": '''def format_price(amount, currency="USD"):
    symbols = {"USD": "$", "EUR": "E", "GBP": "L", "JPY": "Y"}
    symbol = symbols.get(currency, currency + " ")
    if currency == "JPY":
        return symbol + str(int(amount))
    return symbol + "{:.2f}".format(amount)

def format_price_range(low, high, currency="USD"):
    return format_price(low, currency) + " - " + format_price(high, currency)

def format_discount(original, discounted):
    if original <= 0:
        return "N/A"
    pct = ((original - discounted) / original) * 100
    return "{:.0f}% off".format(pct)

def format_receipt_line(name, quantity, unit_price):
    total = quantity * unit_price
    name_part = name[:30].ljust(30)
    qty_part = str(quantity).rjust(4)
    price_part = "${:.2f}".format(unit_price).rjust(10)
    total_part = "${:.2f}".format(total).rjust(12)
    return name_part + qty_part + price_part + total_part

def format_receipt(items):
    lines = []
    grand_total = 0.0
    for item in items:
        name = item.get("name", "Unknown")
        qty = item.get("quantity", 1)
        price = item.get("price", 0)
        lines.append(format_receipt_line(name, qty, price))
        grand_total += qty * price
    lines.append("-" * 56)
    lines.append("TOTAL".ljust(44) + "${:.2f}".format(grand_total).rjust(12))
    return "\\n".join(lines)
''',
    },
    "shopping_cart_checkout": {
        "module_a": '''class ShoppingCart:
    def __init__(self):
        self._items = {}

    def add(self, product_id, name, price, quantity=1):
        if product_id in self._items:
            self._items[product_id]["quantity"] += quantity
        else:
            self._items[product_id] = {
                "name": name, "price": price, "quantity": quantity
            }

    def remove(self, product_id):
        if product_id in self._items:
            del self._items[product_id]

    def update_quantity(self, product_id, quantity):
        if product_id in self._items:
            if quantity <= 0:
                del self._items[product_id]
            else:
                self._items[product_id]["quantity"] = quantity

    def subtotal(self):
        total = 0.0
        for item in self._items.values():
            total += item["price"] * item["quantity"]
        return round(total, 2)

    def item_count(self):
        return sum(item["quantity"] for item in self._items.values())

    def is_empty(self):
        return len(self._items) == 0

    def get_items(self):
        return dict(self._items)

    def clear(self):
        self._items.clear()
''',
        "module_b": '''class Checkout:
    def __init__(self, cart_items):
        self._items = cart_items
        self._discount = 0.0
        self._tax_rate = 0.08
        self._shipping = 0.0

    def set_discount(self, amount):
        self._discount = amount

    def set_tax_rate(self, rate):
        self._tax_rate = rate

    def set_shipping(self, amount):
        self._shipping = amount

    def subtotal(self):
        total = 0.0
        for item in self._items.values():
            total += item["price"] * item["quantity"]
        return round(total, 2)

    def tax_amount(self):
        taxable = self.subtotal() - self._discount
        return round(max(0, taxable) * self._tax_rate, 2)

    def total(self):
        sub = self.subtotal()
        after_discount = max(0, sub - self._discount)
        tax = round(after_discount * self._tax_rate, 2)
        return round(after_discount + tax + self._shipping, 2)

    def order_summary(self):
        return {
            "subtotal": self.subtotal(),
            "discount": self._discount,
            "tax": self.tax_amount(),
            "shipping": self._shipping,
            "total": self.total(),
            "items": len(self._items),
        }
''',
    },
    "user_serializer_deserializer": {
        "module_a": '''def serialize_user(user):
    fields = []
    for key in sorted(user.keys()):
        value = user[key]
        if isinstance(value, list):
            value = "|".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, dict):
            pairs = []
            for k, v in sorted(value.items()):
                pairs.append(str(k) + ":" + str(v))
            value = "|".join(pairs)
        fields.append(str(key) + "=" + str(value))
    return "&".join(fields)

def serialize_user_json_safe(user):
    safe = {}
    sensitive = {"password", "ssn", "credit_card"}
    for key, value in user.items():
        if key in sensitive:
            safe[key] = "***"
        else:
            safe[key] = value
    return safe

def serialize_users_batch(users):
    return "\\n".join(serialize_user(u) for u in users)
''',
        "module_b": '''def deserialize_user(text):
    user = {}
    for pair in text.split("&"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        if value in ("1", "0") and key.startswith("is_"):
            user[key] = value == "1"
        elif "|" in value and ":" in value:
            sub = {}
            for item in value.split("|"):
                if ":" in item:
                    k, v = item.split(":", 1)
                    sub[k] = v
            user[key] = sub
        elif "|" in value:
            user[key] = value.split("|")
        elif value.isdigit():
            user[key] = int(value)
        else:
            user[key] = value
    return user

def deserialize_users_batch(text):
    users = []
    for line in text.strip().split("\\n"):
        if line.strip():
            users.append(deserialize_user(line))
    return users

def merge_user_data(base, update):
    merged = dict(base)
    for key, value in update.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged
''',
    },
    "permission_assigner_checker": {
        "module_a": '''class PermissionAssigner:
    def __init__(self):
        self._roles = {}
        self._user_roles = {}

    def define_role(self, role_name, permissions):
        self._roles[role_name] = set(permissions)

    def assign_role(self, user_id, role_name):
        if user_id not in self._user_roles:
            self._user_roles[user_id] = set()
        self._user_roles[user_id].add(role_name)

    def revoke_role(self, user_id, role_name):
        if user_id in self._user_roles:
            self._user_roles[user_id].discard(role_name)

    def get_user_roles(self, user_id):
        return list(self._user_roles.get(user_id, set()))

    def get_user_permissions(self, user_id):
        perms = set()
        for role in self._user_roles.get(user_id, set()):
            perms.update(self._roles.get(role, set()))
        return list(perms)

    def all_roles(self):
        return list(self._roles.keys())

    def role_permissions(self, role_name):
        return list(self._roles.get(role_name, set()))
''',
        "module_b": '''class PermissionChecker:
    def __init__(self, roles, user_roles):
        self._roles = roles
        self._user_roles = user_roles

    def has_permission(self, user_id, permission):
        for role in self._user_roles.get(user_id, set()):
            if permission in self._roles.get(role, set()):
                return True
        return False

    def has_all_permissions(self, user_id, permissions):
        user_perms = set()
        for role in self._user_roles.get(user_id, set()):
            user_perms.update(self._roles.get(role, set()))
        return all(p in user_perms for p in permissions)

    def has_any_permission(self, user_id, permissions):
        user_perms = set()
        for role in self._user_roles.get(user_id, set()):
            user_perms.update(self._roles.get(role, set()))
        return any(p in user_perms for p in permissions)

    def check_access(self, user_id, resource, action):
        permission = resource + ":" + action
        return self.has_permission(user_id, permission)

    def audit_user(self, user_id):
        roles = list(self._user_roles.get(user_id, set()))
        perms = set()
        for role in roles:
            perms.update(self._roles.get(role, set()))
        return {"user": user_id, "roles": roles, "permissions": sorted(perms)}
''',
    },
    "notification_builder_renderer": {
        "module_a": '''class NotificationBuilder:
    def __init__(self):
        self._title = ""
        self._body = ""
        self._priority = "normal"
        self._tags = []
        self._data = {}

    def set_title(self, title):
        self._title = title
        return self

    def set_body(self, body):
        self._body = body
        return self

    def set_priority(self, priority):
        self._priority = priority
        return self

    def add_tag(self, tag):
        self._tags.append(tag)
        return self

    def set_data(self, key, value):
        self._data[key] = value
        return self

    def build(self):
        return {
            "title": self._title,
            "body": self._body,
            "priority": self._priority,
            "tags": list(self._tags),
            "data": dict(self._data),
        }
''',
        "module_b": '''class NotificationRenderer:
    def __init__(self):
        self._templates = {}

    def register_template(self, name, template):
        self._templates[name] = template

    def render_text(self, notification):
        title = notification.get("title", "")
        body = notification.get("body", "")
        priority = notification.get("priority", "normal")
        prefix = "[!] " if priority == "high" else ""
        return prefix + title + ": " + body

    def render_html(self, notification):
        title = notification.get("title", "")
        body = notification.get("body", "")
        tags = notification.get("tags", [])
        html = "<div class='notification'>"
        html += "<h3>" + title + "</h3>"
        html += "<p>" + body + "</p>"
        if tags:
            html += "<div class='tags'>"
            for tag in tags:
                html += "<span class='tag'>" + tag + "</span>"
            html += "</div>"
        html += "</div>"
        return html

    def render_summary(self, notifications):
        lines = []
        for n in notifications:
            lines.append("- " + n.get("title", "Untitled"))
        return "\\n".join(lines)
''',
    },
    "invoice_generator_validator": {
        "module_a": '''class InvoiceGenerator:
    def __init__(self, invoice_number):
        self._number = invoice_number
        self._items = []
        self._customer = {}
        self._notes = ""

    def set_customer(self, name, address, email):
        self._customer = {
            "name": name, "address": address, "email": email
        }

    def add_line_item(self, description, quantity, unit_price):
        self._items.append({
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": round(quantity * unit_price, 2),
        })

    def set_notes(self, notes):
        self._notes = notes

    def subtotal(self):
        return round(sum(item["total"] for item in self._items), 2)

    def generate(self, tax_rate=0.0):
        sub = self.subtotal()
        tax = round(sub * tax_rate, 2)
        return {
            "invoice_number": self._number,
            "customer": self._customer,
            "items": self._items,
            "subtotal": sub,
            "tax": tax,
            "total": round(sub + tax, 2),
            "notes": self._notes,
        }
''',
        "module_b": '''class InvoiceValidator:
    def __init__(self):
        self._errors = []

    def validate(self, invoice):
        self._errors = []
        if not invoice.get("invoice_number"):
            self._errors.append("missing invoice number")
        customer = invoice.get("customer", {})
        if not customer.get("name"):
            self._errors.append("missing customer name")
        if not customer.get("email"):
            self._errors.append("missing customer email")
        items = invoice.get("items", [])
        if not items:
            self._errors.append("no line items")
        expected_subtotal = 0.0
        for i, item in enumerate(items):
            if item.get("quantity", 0) <= 0:
                self._errors.append("item {} has invalid quantity".format(i))
            if item.get("unit_price", 0) < 0:
                self._errors.append("item {} has negative price".format(i))
            expected_subtotal += item.get("total", 0)
        actual_subtotal = invoice.get("subtotal", 0)
        if abs(expected_subtotal - actual_subtotal) > 0.01:
            self._errors.append("subtotal mismatch")
        return len(self._errors) == 0

    def get_errors(self):
        return list(self._errors)

    def is_valid(self):
        return len(self._errors) == 0
''',
    },
    "tax_calculator_formatter": {
        "module_a": '''_TAX_RATES = {
    "CA": 0.0725, "NY": 0.08, "TX": 0.0625,
    "FL": 0.06, "WA": 0.065, "OR": 0.0,
    "IL": 0.0625, "PA": 0.06, "OH": 0.0575,
    "GA": 0.04, "NC": 0.0475, "NJ": 0.06625,
}

def calculate_sales_tax(amount, state):
    rate = _TAX_RATES.get(state.upper(), 0.0)
    return round(amount * rate, 2)

def calculate_income_tax(income):
    brackets = [
        (10275, 0.10), (41775, 0.12), (89075, 0.22),
        (170050, 0.24), (215950, 0.32), (539900, 0.35),
        (float("inf"), 0.37),
    ]
    tax = 0.0
    prev_limit = 0
    for limit, rate in brackets:
        if income <= prev_limit:
            break
        taxable = min(income, limit) - prev_limit
        tax += taxable * rate
        prev_limit = limit
    return round(tax, 2)

def effective_tax_rate(income):
    if income <= 0:
        return 0.0
    tax = calculate_income_tax(income)
    return round(tax / income * 100, 2)

def get_tax_rate(state):
    return _TAX_RATES.get(state.upper(), 0.0)
''',
        "module_b": '''def format_tax_amount(amount, label="Tax"):
    return "{}: ${:,.2f}".format(label, amount)

def format_tax_breakdown(subtotal, tax, total):
    lines = []
    lines.append("Subtotal: ${:,.2f}".format(subtotal))
    lines.append("Tax:      ${:,.2f}".format(tax))
    lines.append("-" * 25)
    lines.append("Total:    ${:,.2f}".format(total))
    return "\\n".join(lines)

def format_tax_rate(rate):
    return "{:.2f}%".format(rate * 100)

def format_income_tax_summary(income, tax):
    effective = (tax / income * 100) if income > 0 else 0
    lines = []
    lines.append("Gross Income:     ${:>12,.2f}".format(income))
    lines.append("Federal Tax:      ${:>12,.2f}".format(tax))
    lines.append("After Tax Income: ${:>12,.2f}".format(income - tax))
    lines.append("Effective Rate:   {:>12.2f}%".format(effective))
    return "\\n".join(lines)

def format_tax_comparison(income, states):
    lines = ["Tax Comparison for ${:,.2f}:".format(income)]
    rates = {
        "CA": 0.0725, "NY": 0.08, "TX": 0.0625,
        "FL": 0.06, "WA": 0.065, "OR": 0.0,
    }
    for state in states:
        rate = rates.get(state, 0.0)
        tax = round(income * rate, 2)
        lines.append("  {}: ${:,.2f} ({:.2f}%)".format(
            state, tax, rate * 100))
    return "\\n".join(lines)
''',
    },
    "coupon_generator_redeemer": {
        "module_a": '''import random as _coupon_random

def generate_coupon_code(length=8):
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = ""
    for i in range(length):
        code += _coupon_random.choice(chars)
    return code

def generate_coupon(discount_type, value, min_purchase=0, max_uses=1):
    code = generate_coupon_code()
    return {
        "code": code,
        "type": discount_type,
        "value": value,
        "min_purchase": min_purchase,
        "max_uses": max_uses,
        "uses": 0,
        "active": True,
    }

def generate_bulk_coupons(count, discount_type, value, min_purchase=0):
    coupons = {}
    for i in range(count):
        coupon = generate_coupon(discount_type, value, min_purchase)
        coupons[coupon["code"]] = coupon
    return coupons

def is_coupon_valid(coupon):
    if not coupon.get("active", False):
        return False
    if coupon.get("uses", 0) >= coupon.get("max_uses", 1):
        return False
    return True
''',
        "module_b": '''class CouponRedeemer:
    def __init__(self, coupon_store):
        self._store = coupon_store
        self._redemptions = []

    def redeem(self, code, order_total):
        coupon = self._store.get(code)
        if coupon is None:
            return {"success": False, "error": "invalid code"}
        if not coupon.get("active", False):
            return {"success": False, "error": "coupon inactive"}
        if coupon.get("uses", 0) >= coupon.get("max_uses", 1):
            return {"success": False, "error": "coupon exhausted"}
        if order_total < coupon.get("min_purchase", 0):
            return {"success": False, "error": "minimum not met"}
        if coupon["type"] == "percent":
            discount = round(order_total * coupon["value"] / 100, 2)
        elif coupon["type"] == "fixed":
            discount = min(coupon["value"], order_total)
        else:
            discount = 0.0
        coupon["uses"] += 1
        self._redemptions.append({"code": code, "discount": discount})
        new_total = round(order_total - discount, 2)
        return {"success": True, "discount": discount, "new_total": new_total}

    def get_redemptions(self):
        return list(self._redemptions)

    def total_savings(self):
        return sum(r["discount"] for r in self._redemptions)
''',
    },
    "address_normalizer_geocoder": {
        "module_a": '''def normalize_address(address):
    address = address.strip()
    replacements = {
        " st ": " Street ", " st.": " Street",
        " ave ": " Avenue ", " ave.": " Avenue",
        " blvd ": " Boulevard ", " blvd.": " Boulevard",
        " dr ": " Drive ", " dr.": " Drive",
        " ln ": " Lane ", " ln.": " Lane",
        " rd ": " Road ", " rd.": " Road",
        " ct ": " Court ", " ct.": " Court",
    }
    lower = " " + address.lower() + " "
    for abbrev, full in replacements.items():
        lower = lower.replace(abbrev, full.lower())
    words = lower.strip().split()
    return " ".join(w.capitalize() for w in words)

def parse_address(address):
    parts = address.split(",")
    result = {"street": "", "city": "", "state": "", "zip": ""}
    if len(parts) >= 1:
        result["street"] = parts[0].strip()
    if len(parts) >= 2:
        result["city"] = parts[1].strip()
    if len(parts) >= 3:
        state_zip = parts[2].strip().split()
        if state_zip:
            result["state"] = state_zip[0]
        if len(state_zip) > 1:
            result["zip"] = state_zip[1]
    return result

def format_address(street, city, state, zipcode):
    return "{}, {}, {} {}".format(street, city, state, zipcode)
''',
        "module_b": '''def simple_geocode(address):
    city_coords = {
        "new york": (40.7128, -74.0060),
        "los angeles": (34.0522, -118.2437),
        "chicago": (41.8781, -87.6298),
        "houston": (29.7604, -95.3698),
        "phoenix": (33.4484, -112.0740),
        "philadelphia": (39.9526, -75.1652),
        "san antonio": (29.4241, -98.4936),
        "san diego": (32.7157, -117.1611),
        "dallas": (32.7767, -96.7970),
        "san jose": (37.3382, -121.8863),
    }
    lower = address.lower()
    for city, coords in city_coords.items():
        if city in lower:
            return {"lat": coords[0], "lon": coords[1], "found": True}
    return {"lat": 0.0, "lon": 0.0, "found": False}

def haversine_distance(lat1, lon1, lat2, lon2):
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 2)

def format_coordinates(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return "{:.4f}{} {:.4f}{}".format(abs(lat), ns, abs(lon), ew)
''',
    },
    "stack_operations_evaluator": {
        "module_a": '''class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        if not self._items:
            return None
        return self._items[-1]

    def is_empty(self):
        return len(self._items) == 0

    def size(self):
        return len(self._items)

    def clear(self):
        self._items.clear()

    def to_list(self):
        return list(self._items)

    def contains(self, item):
        return item in self._items

    def reverse(self):
        self._items.reverse()
''',
        "module_b": '''def evaluate_postfix(expression):
    stack = []
    tokens = expression.split()
    operators = {"+", "-", "*", "/"}
    for token in tokens:
        if token in operators:
            if len(stack) < 2:
                return None
            b = stack.pop()
            a = stack.pop()
            if token == "+":
                stack.append(a + b)
            elif token == "-":
                stack.append(a - b)
            elif token == "*":
                stack.append(a * b)
            elif token == "/":
                if b == 0:
                    return None
                stack.append(a / b)
        else:
            try:
                stack.append(float(token))
            except ValueError:
                return None
    if len(stack) == 1:
        return stack[0]
    return None

def infix_to_postfix(expression):
    precedence = {"+": 1, "-": 1, "*": 2, "/": 2}
    output = []
    op_stack = []
    tokens = expression.replace("(", " ( ").replace(")", " ) ").split()
    for token in tokens:
        if token in precedence:
            while (op_stack and op_stack[-1] != "(" and
                   precedence.get(op_stack[-1], 0) >= precedence[token]):
                output.append(op_stack.pop())
            op_stack.append(token)
        elif token == "(":
            op_stack.append(token)
        elif token == ")":
            while op_stack and op_stack[-1] != "(":
                output.append(op_stack.pop())
            if op_stack:
                op_stack.pop()
        else:
            output.append(token)
    while op_stack:
        output.append(op_stack.pop())
    return " ".join(output)
''',
    },
    "graph_builder_traverser": {
        "module_a": '''class GraphBuilder:
    def __init__(self, directed=False):
        self._adj = {}
        self._directed = directed

    def add_node(self, node):
        if node not in self._adj:
            self._adj[node] = []

    def add_edge(self, src, dst, weight=1):
        self.add_node(src)
        self.add_node(dst)
        self._adj[src].append((dst, weight))
        if not self._directed:
            self._adj[dst].append((src, weight))

    def remove_edge(self, src, dst):
        if src in self._adj:
            self._adj[src] = [(d, w) for d, w in self._adj[src] if d != dst]
        if not self._directed and dst in self._adj:
            self._adj[dst] = [(d, w) for d, w in self._adj[dst] if d != src]

    def neighbors(self, node):
        return [n for n, w in self._adj.get(node, [])]

    def node_count(self):
        return len(self._adj)

    def edge_count(self):
        total = sum(len(edges) for edges in self._adj.values())
        if not self._directed:
            total //= 2
        return total

    def get_adjacency(self):
        return dict(self._adj)
''',
        "module_b": '''class GraphTraverser:
    def __init__(self, adjacency):
        self._adj = adjacency

    def bfs(self, start):
        visited = []
        queue = [start]
        seen = {start}
        while queue:
            node = queue.pop(0)
            visited.append(node)
            for neighbor, weight in self._adj.get(node, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return visited

    def dfs(self, start):
        visited = []
        stack = [start]
        seen = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            visited.append(node)
            for neighbor, weight in reversed(self._adj.get(node, [])):
                if neighbor not in seen:
                    stack.append(neighbor)
        return visited

    def has_path(self, start, end):
        visited = set()
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node == end:
                return True
            visited.add(node)
            for neighbor, w in self._adj.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        return False

    def connected_components(self):
        visited = set()
        components = []
        for node in self._adj:
            if node not in visited:
                component = self.bfs(node)
                visited.update(component)
                components.append(component)
        return components
''',
    },
    "matrix_constructor_multiplier": {
        "module_a": '''def zeros(rows, cols):
    return [[0] * cols for _ in range(rows)]

def identity(n):
    mat = zeros(n, n)
    for i in range(n):
        mat[i][i] = 1
    return mat

def from_list(data, rows, cols):
    mat = zeros(rows, cols)
    idx = 0
    for i in range(rows):
        for j in range(cols):
            if idx < len(data):
                mat[i][j] = data[idx]
                idx += 1
    return mat

def transpose(matrix):
    if not matrix:
        return []
    rows = len(matrix)
    cols = len(matrix[0])
    result = zeros(cols, rows)
    for i in range(rows):
        for j in range(cols):
            result[j][i] = matrix[i][j]
    return result

def diagonal(matrix):
    n = min(len(matrix), len(matrix[0])) if matrix else 0
    return [matrix[i][i] for i in range(n)]

def trace(matrix):
    return sum(diagonal(matrix))
''',
        "module_b": '''def multiply(A, B):
    if not A or not B:
        return []
    rows_a = len(A)
    cols_a = len(A[0])
    cols_b = len(B[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            s = 0
            for k in range(cols_a):
                s += A[i][k] * B[k][j]
            result[i][j] = s
    return result

def scalar_multiply(matrix, scalar):
    return [[cell * scalar for cell in row] for row in matrix]

def add_matrices(A, B):
    rows = len(A)
    cols = len(A[0])
    result = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            result[i][j] = A[i][j] + B[i][j]
    return result

def determinant_2x2(matrix):
    return matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]

def matrix_power(matrix, n):
    size = len(matrix)
    result = [[1 if i == j else 0 for j in range(size)] for i in range(size)]
    base = [row[:] for row in matrix]
    while n > 0:
        if n % 2 == 1:
            result = multiply(result, base)
        base = multiply(base, base)
        n //= 2
    return result
''',
    },
    "stats_collector_reporter": {
        "module_a": '''class StatsCollector:
    def __init__(self):
        self._data = {}

    def add(self, metric, value):
        if metric not in self._data:
            self._data[metric] = []
        self._data[metric].append(value)

    def add_batch(self, metric, values):
        if metric not in self._data:
            self._data[metric] = []
        self._data[metric].extend(values)

    def get_values(self, metric):
        return list(self._data.get(metric, []))

    def metrics(self):
        return list(self._data.keys())

    def count(self, metric):
        return len(self._data.get(metric, []))

    def clear(self, metric=None):
        if metric is None:
            self._data.clear()
        elif metric in self._data:
            del self._data[metric]

    def total(self, metric):
        return sum(self._data.get(metric, []))

    def latest(self, metric, n=1):
        values = self._data.get(metric, [])
        return values[-n:] if values else []
''',
        "module_b": '''class StatsReporter:
    def __init__(self, data):
        self._data = data

    def mean(self, metric):
        values = self._data.get(metric, [])
        if not values:
            return 0.0
        return sum(values) / len(values)

    def median(self, metric):
        values = sorted(self._data.get(metric, []))
        n = len(values)
        if n == 0:
            return 0.0
        if n % 2 == 1:
            return values[n // 2]
        return (values[n // 2 - 1] + values[n // 2]) / 2.0

    def std_dev(self, metric):
        values = self._data.get(metric, [])
        if len(values) < 2:
            return 0.0
        avg = sum(values) / len(values)
        variance = sum((v - avg) ** 2 for v in values) / (len(values) - 1)
        return variance ** 0.5

    def percentile(self, metric, pct):
        values = sorted(self._data.get(metric, []))
        if not values:
            return 0.0
        idx = int(len(values) * pct / 100)
        idx = min(idx, len(values) - 1)
        return values[idx]

    def summary(self, metric):
        values = self._data.get(metric, [])
        if not values:
            return {}
        return {
            "count": len(values),
            "mean": round(self.mean(metric), 4),
            "median": round(self.median(metric), 4),
            "std_dev": round(self.std_dev(metric), 4),
            "min": min(values),
            "max": max(values),
        }
''',
    },
    "text_indexer_searcher": {
        "module_a": '''class TextIndexer:
    def __init__(self):
        self._index = {}
        self._docs = {}
        self._doc_count = 0

    def add_document(self, doc_id, text):
        self._docs[doc_id] = text
        self._doc_count += 1
        words = text.lower().split()
        for pos, word in enumerate(words):
            word = word.strip(".,!?;:")
            if word not in self._index:
                self._index[word] = {}
            if doc_id not in self._index[word]:
                self._index[word][doc_id] = []
            self._index[word][doc_id].append(pos)

    def remove_document(self, doc_id):
        if doc_id in self._docs:
            del self._docs[doc_id]
            self._doc_count -= 1
            for word in list(self._index.keys()):
                if doc_id in self._index[word]:
                    del self._index[word][doc_id]
                if not self._index[word]:
                    del self._index[word]

    def get_index(self):
        return dict(self._index)

    def document_count(self):
        return self._doc_count

    def vocabulary_size(self):
        return len(self._index)
''',
        "module_b": '''class TextSearcher:
    def __init__(self, index, docs):
        self._index = index
        self._docs = docs

    def search(self, query):
        words = query.lower().split()
        if not words:
            return []
        doc_scores = {}
        for word in words:
            word = word.strip(".,!?;:")
            if word in self._index:
                for doc_id, positions in self._index[word].items():
                    if doc_id not in doc_scores:
                        doc_scores[doc_id] = 0
                    doc_scores[doc_id] += len(positions)
        results = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
        return [(doc_id, score) for doc_id, score in results]

    def search_exact(self, phrase):
        words = phrase.lower().split()
        if not words or words[0] not in self._index:
            return []
        candidates = set(self._index[words[0]].keys())
        for word in words[1:]:
            if word in self._index:
                candidates &= set(self._index[word].keys())
            else:
                return []
        return list(candidates)

    def get_document(self, doc_id):
        return self._docs.get(doc_id, "")

    def highlight(self, doc_id, query):
        text = self._docs.get(doc_id, "")
        words = query.lower().split()
        result = text
        for word in words:
            result = result.replace(word, "[" + word + "]")
        return result
''',
    },
    "trie_builder_autocomplete": {
        "module_a": '''class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end = False
        self.count = 0

class TrieBuilder:
    def __init__(self):
        self.root = TrieNode()
        self._word_count = 0

    def insert(self, word):
        node = self.root
        for ch in word.lower():
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        if not node.is_end:
            self._word_count += 1
        node.is_end = True
        node.count += 1

    def insert_batch(self, words):
        for word in words:
            self.insert(word)

    def contains(self, word):
        node = self.root
        for ch in word.lower():
            if ch not in node.children:
                return False
            node = node.children[ch]
        return node.is_end

    def starts_with(self, prefix):
        node = self.root
        for ch in prefix.lower():
            if ch not in node.children:
                return False
            node = node.children[ch]
        return True

    def word_count(self):
        return self._word_count

    def get_root(self):
        return self.root
''',
        "module_b": '''class AutoCompleter:
    def __init__(self, trie_root):
        self._root = trie_root

    def complete(self, prefix, max_results=10):
        node = self._root
        for ch in prefix.lower():
            if ch not in node.children:
                return []
            node = node.children[ch]
        results = []
        self._collect(node, prefix.lower(), results, max_results)
        return results

    def _collect(self, node, current, results, max_results):
        if len(results) >= max_results:
            return
        if node.is_end:
            results.append(current)
        for ch in sorted(node.children.keys()):
            if len(results) >= max_results:
                return
            self._collect(node.children[ch], current + ch, results, max_results)

    def complete_ranked(self, prefix, max_results=10):
        node = self._root
        for ch in prefix.lower():
            if ch not in node.children:
                return []
            node = node.children[ch]
        candidates = []
        self._collect_ranked(node, prefix.lower(), candidates)
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [word for word, count in candidates[:max_results]]

    def _collect_ranked(self, node, current, results):
        if node.is_end:
            results.append((current, node.count))
        for ch in node.children:
            self._collect_ranked(node.children[ch], current + ch, results)
''',
    },
    "priority_queue_scheduler": {
        "module_a": '''class PriorityQueue:
    def __init__(self):
        self._heap = []

    def push(self, item, priority):
        entry = (priority, len(self._heap), item)
        self._heap.append(entry)
        self._sift_up(len(self._heap) - 1)

    def pop(self):
        if not self._heap:
            raise IndexError("pop from empty queue")
        self._swap(0, len(self._heap) - 1)
        priority, _, item = self._heap.pop()
        if self._heap:
            self._sift_down(0)
        return item

    def peek(self):
        if not self._heap:
            return None
        return self._heap[0][2]

    def _sift_up(self, idx):
        while idx > 0:
            parent = (idx - 1) // 2
            if self._heap[idx][0] < self._heap[parent][0]:
                self._swap(idx, parent)
                idx = parent
            else:
                break

    def _sift_down(self, idx):
        n = len(self._heap)
        while True:
            smallest = idx
            left = 2 * idx + 1
            right = 2 * idx + 2
            if left < n and self._heap[left][0] < self._heap[smallest][0]:
                smallest = left
            if right < n and self._heap[right][0] < self._heap[smallest][0]:
                smallest = right
            if smallest != idx:
                self._swap(idx, smallest)
                idx = smallest
            else:
                break

    def _swap(self, i, j):
        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]

    def size(self):
        return len(self._heap)

    def is_empty(self):
        return len(self._heap) == 0
''',
        "module_b": '''class TaskScheduler:
    def __init__(self):
        self._tasks = []
        self._completed = []

    def add_task(self, name, priority, duration):
        self._tasks.append({
            "name": name,
            "priority": priority,
            "duration": duration,
            "status": "pending",
        })

    def schedule(self):
        pending = [t for t in self._tasks if t["status"] == "pending"]
        pending.sort(key=lambda t: t["priority"])
        timeline = []
        current_time = 0
        for task in pending:
            timeline.append({
                "name": task["name"],
                "start": current_time,
                "end": current_time + task["duration"],
            })
            current_time += task["duration"]
        return timeline

    def complete_task(self, name):
        for task in self._tasks:
            if task["name"] == name:
                task["status"] = "completed"
                self._completed.append(name)
                return True
        return False

    def pending_tasks(self):
        return [t for t in self._tasks if t["status"] == "pending"]

    def completed_tasks(self):
        return list(self._completed)

    def total_duration(self):
        return sum(t["duration"] for t in self._tasks if t["status"] == "pending")
''',
    },
    "bloom_filter_checker": {
        "module_a": '''class BloomFilter:
    def __init__(self, size=1000, num_hashes=3):
        self._size = size
        self._num_hashes = num_hashes
        self._bits = [False] * size
        self._count = 0

    def _hashes(self, item):
        item_str = str(item)
        indices = []
        for i in range(self._num_hashes):
            h = 0
            for ch in item_str:
                h = (h * 31 + ord(ch) + i * 7) % self._size
            indices.append(h)
        return indices

    def add(self, item):
        for idx in self._hashes(item):
            self._bits[idx] = True
        self._count += 1

    def add_batch(self, items):
        for item in items:
            self.add(item)

    def might_contain(self, item):
        return all(self._bits[idx] for idx in self._hashes(item))

    def count(self):
        return self._count

    def fill_ratio(self):
        set_bits = sum(1 for b in self._bits if b)
        return set_bits / self._size

    def estimated_false_positive_rate(self):
        ratio = self.fill_ratio()
        return ratio ** self._num_hashes
''',
        "module_b": '''class SetChecker:
    def __init__(self, bloom_filter, exact_set=None):
        self._bloom = bloom_filter
        self._exact = exact_set or set()

    def check(self, item):
        if not self._bloom.might_contain(item):
            return {"status": "definitely_not_present"}
        if self._exact and item in self._exact:
            return {"status": "present"}
        return {"status": "possibly_present"}

    def check_batch(self, items):
        results = {}
        for item in items:
            results[item] = self.check(item)
        return results

    def false_positive_check(self, items):
        false_positives = 0
        true_negatives = 0
        for item in items:
            bloom_says = self._bloom.might_contain(item)
            actually_in = item in self._exact
            if bloom_says and not actually_in:
                false_positives += 1
            elif not bloom_says and not actually_in:
                true_negatives += 1
        total = false_positives + true_negatives
        fp_rate = false_positives / total if total > 0 else 0.0
        return {
            "false_positives": false_positives,
            "true_negatives": true_negatives,
            "fp_rate": round(fp_rate, 4),
        }

    def stats(self):
        return {
            "bloom_count": self._bloom.count(),
            "exact_size": len(self._exact),
            "fill_ratio": round(self._bloom.fill_ratio(), 4),
        }
''',
    },
    "linked_list_reverser": {
        "module_a": '''class ListNode:
    def __init__(self, value, next_node=None):
        self.value = value
        self.next_node = next_node

class LinkedList:
    def __init__(self):
        self.head = None
        self._size = 0

    def push_front(self, value):
        self.head = ListNode(value, self.head)
        self._size += 1

    def push_back(self, value):
        new_node = ListNode(value)
        if self.head is None:
            self.head = new_node
        else:
            current = self.head
            while current.next_node:
                current = current.next_node
            current.next_node = new_node
        self._size += 1

    def pop_front(self):
        if self.head is None:
            raise IndexError("pop from empty list")
        value = self.head.value
        self.head = self.head.next_node
        self._size -= 1
        return value

    def to_list(self):
        result = []
        current = self.head
        while current:
            result.append(current.value)
            current = current.next_node
        return result

    def size(self):
        return self._size

    def contains(self, value):
        current = self.head
        while current:
            if current.value == value:
                return True
            current = current.next_node
        return False
''',
        "module_b": '''def reverse_linked_list(head):
    prev = None
    current = head
    while current:
        next_node = current.next_node
        current.next_node = prev
        prev = current
        current = next_node
    return prev

def reverse_in_groups(head, k):
    if head is None or k <= 1:
        return head
    current = head
    prev_tail = None
    new_head = None
    while current:
        group_head = current
        group_prev = None
        count = 0
        while current and count < k:
            next_node = current.next_node
            current.next_node = group_prev
            group_prev = current
            current = next_node
            count += 1
        if new_head is None:
            new_head = group_prev
        if prev_tail:
            prev_tail.next_node = group_prev
        prev_tail = group_head
    return new_head

def find_middle(head):
    if head is None:
        return None
    slow = head
    fast = head
    while fast.next_node and fast.next_node.next_node:
        slow = slow.next_node
        fast = fast.next_node.next_node
    return slow

def detect_cycle(head):
    slow = head
    fast = head
    while fast and fast.next_node:
        slow = slow.next_node
        fast = fast.next_node.next_node
        if slow is fast:
            return True
    return False
''',
    },
    "binary_tree_serializer": {
        "module_a": '''class TreeNode:
    def __init__(self, value, left=None, right=None):
        self.value = value
        self.left = left
        self.right = right

class BinaryTree:
    def __init__(self):
        self.root = None

    def insert(self, value):
        if self.root is None:
            self.root = TreeNode(value)
        else:
            self._insert_rec(self.root, value)

    def _insert_rec(self, node, value):
        if value < node.value:
            if node.left is None:
                node.left = TreeNode(value)
            else:
                self._insert_rec(node.left, value)
        else:
            if node.right is None:
                node.right = TreeNode(value)
            else:
                self._insert_rec(node.right, value)

    def inorder(self):
        result = []
        self._inorder_rec(self.root, result)
        return result

    def _inorder_rec(self, node, result):
        if node:
            self._inorder_rec(node.left, result)
            result.append(node.value)
            self._inorder_rec(node.right, result)

    def height(self):
        return self._height_rec(self.root)

    def _height_rec(self, node):
        if node is None:
            return 0
        return 1 + max(self._height_rec(node.left), self._height_rec(node.right))

    def search(self, value):
        return self._search_rec(self.root, value)

    def _search_rec(self, node, value):
        if node is None:
            return False
        if value == node.value:
            return True
        if value < node.value:
            return self._search_rec(node.left, value)
        return self._search_rec(node.right, value)
''',
        "module_b": '''def serialize_tree(root):
    if root is None:
        return "null"
    result = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node is None:
            result.append("null")
        else:
            result.append(str(node.value))
            queue.append(node.left)
            queue.append(node.right)
    while result and result[-1] == "null":
        result.pop()
    return ",".join(result)

def deserialize_tree_node(value, left=None, right=None):
    class TNode:
        def __init__(self, v, l=None, r=None):
            self.value = v
            self.left = l
            self.right = r
    return TNode(value, left, right)

def deserialize_tree(data):
    if not data or data == "null":
        return None
    values = data.split(",")
    root = deserialize_tree_node(int(values[0]))
    queue = [root]
    i = 1
    while queue and i < len(values):
        node = queue.pop(0)
        if i < len(values) and values[i] != "null":
            node.left = deserialize_tree_node(int(values[i]))
            queue.append(node.left)
        i += 1
        if i < len(values) and values[i] != "null":
            node.right = deserialize_tree_node(int(values[i]))
            queue.append(node.right)
        i += 1
    return root

def tree_to_dict(root):
    if root is None:
        return None
    return {
        "value": root.value,
        "left": tree_to_dict(root.left),
        "right": tree_to_dict(root.right),
    }
''',
    },
}


# -- Extraction helpers ----------------------------------------------------

def extract_obs(prove_objs):
    """Extract obstructions list from prove JSON output."""
    obs = []
    if prove_objs and isinstance(prove_objs[0], dict):
        for finfo in prove_objs[0].get("files", []):
            obs.extend(finfo.get("obstructions", []))
    return obs


def extract_verdict(prove_objs):
    """Extract verdict string from prove JSON output."""
    if prove_objs and isinstance(prove_objs[0], dict):
        for finfo in prove_objs[0].get("files", []):
            return finfo.get("verdict", "unknown")
    return "unknown"


def extract_certificate(prove_objs):
    """Extract certificate from prove JSON output."""
    if prove_objs and isinstance(prove_objs[0], dict):
        for finfo in prove_objs[0].get("files", []):
            return finfo.get("certificate", {})
    return {}


def extract_coords(prove_objs):
    """Extract coordinate count from prove JSON output."""
    if prove_objs and isinstance(prove_objs[0], dict):
        for finfo in prove_objs[0].get("files", []):
            return len(finfo.get("coordinates", []))
    return 0


def extract_props(prove_objs):
    """Extract proposition count from prove JSON output."""
    if prove_objs and isinstance(prove_objs[0], dict):
        for finfo in prove_objs[0].get("files", []):
            return finfo.get("propositions_total", 0)
    return 0


# -- Core experiment -------------------------------------------------------

def run_pair(name, pair):
    """Run a single pair through prove, equiv, and strategy comparison."""
    temps = []
    module_a = pair["module_a"]
    module_b = pair["module_b"]
    combined = module_a + "\n" + module_b

    fa = write_temp_py(module_a); temps.append(fa)
    fb = write_temp_py(module_b); temps.append(fb)
    fc = write_temp_py(combined); temps.append(fc)

    # (a) Individual verification
    t0 = time.perf_counter()
    prove_a = run_jugeo("prove", fa)
    time_a = time.perf_counter() - t0

    t0 = time.perf_counter()
    prove_b = run_jugeo("prove", fb)
    time_b = time.perf_counter() - t0

    # (b) Combined verification
    t0 = time.perf_counter()
    prove_c = run_jugeo("prove", fc)
    time_c = time.perf_counter() - t0

    obs_a = extract_obs(prove_a)
    obs_b = extract_obs(prove_b)
    obs_c = extract_obs(prove_c)

    # (c) Equivalence check
    t0 = time.perf_counter()
    equiv_result = run_jugeo("equiv", fa, fb)
    time_equiv = time.perf_counter() - t0

    equiv_verdict = "unknown"
    equiv_obs = []
    if equiv_result and isinstance(equiv_result[0], dict):
        equiv_verdict = equiv_result[0].get("verdict", "unknown")
        equiv_obs = equiv_result[0].get("obstructions", [])

    # (d) Strategy comparison (eager / exhaustive / iterative)
    strategy_results = {}
    for strat in ["eager", "exhaustive", "iterative"]:
        t0 = time.perf_counter()
        prove_s = run_jugeo("prove", fc, "--strategy", strat)
        elapsed = time.perf_counter() - t0
        strategy_results[strat] = {
            "verdict": extract_verdict(prove_s),
            "obstructions": len(extract_obs(prove_s)),
            "coordinates": extract_coords(prove_s),
            "propositions": extract_props(prove_s),
            "time_s": round(elapsed, 4),
        }

    # Convergence: increasing max-depth
    convergence = []
    for depth in range(1, 8):
        prove_d = run_jugeo("prove", fc, "--max-depth", str(depth))
        n_obs = len(extract_obs(prove_d))
        convergence.append({"depth": depth, "obstructions": n_obs})

    obs_seq = [c["obstructions"] for c in convergence]
    monotonic = all(obs_seq[i] >= obs_seq[i+1] for i in range(len(obs_seq)-1))

    for t in temps:
        try: os.unlink(t)
        except OSError: pass

    return {
        "pair": name,
        "individual_obs_a": len(obs_a),
        "individual_obs_b": len(obs_b),
        "combined_obs": len(obs_c),
        "verdict_a": extract_verdict(prove_a),
        "verdict_b": extract_verdict(prove_b),
        "verdict_combined": extract_verdict(prove_c),
        "time_a_s": round(time_a, 4),
        "time_b_s": round(time_b, 4),
        "time_combined_s": round(time_c, 4),
        "equiv_verdict": equiv_verdict,
        "equiv_obstructions": len(equiv_obs),
        "time_equiv_s": round(time_equiv, 4),
        "strategy_comparison": strategy_results,
        "convergence": convergence,
        "monotonic_decrease": monotonic,
    }


def main():
    print("=" * 72)
    print("Paper 8: Automated Interface Reconciliation for Modular Verification")
    print("=" * 72)

    # Validate all programs parse
    for name, pair in PAIRS.items():
        ast.parse(pair["module_a"])
        ast.parse(pair["module_b"])
    print(f"Validated {len(PAIRS)} pairs ({len(PAIRS)*2} modules)")

    results = {"pairs": [], "literature_baselines": LITERATURE_BASELINES}
    total_converged = 0

    for name, pair in PAIRS.items():
        t0 = time.perf_counter()
        r = run_pair(name, pair)
        r["total_time_s"] = round(time.perf_counter() - t0, 3)
        results["pairs"].append(r)

        if r["monotonic_decrease"]:
            total_converged += 1

        print(f"\n  {name}:")
        print(f"    Obs: A={r['individual_obs_a']} B={r['individual_obs_b']} "
              f"combined={r['combined_obs']}")
        print(f"    Verdicts: A={r['verdict_a']} B={r['verdict_b']} "
              f"combined={r['verdict_combined']}")
        print(f"    Equiv: {r['equiv_verdict']} ({r['equiv_obstructions']} obs)")
        print(f"    Convergence monotonic: {r['monotonic_decrease']}")
        for strat, sd in r["strategy_comparison"].items():
            print(f"      {strat:12s}: verdict={sd['verdict']} obs={sd['obstructions']} "
                  f"time={sd['time_s']:.4f}s")
        print(f"    Time: {r['total_time_s']:.3f}s")

    n = len(PAIRS)
    all_obs_a = [r["individual_obs_a"] for r in results["pairs"]]
    all_obs_b = [r["individual_obs_b"] for r in results["pairs"]]
    all_obs_c = [r["combined_obs"] for r in results["pairs"]]

    summary = {
        "total_pairs": n,
        "total_modules": n * 2,
        "convergence_rate": total_converged / n if n else 0,
        "mean_obs_a": round(statistics.mean(all_obs_a), 4) if all_obs_a else 0,
        "mean_obs_b": round(statistics.mean(all_obs_b), 4) if all_obs_b else 0,
        "mean_obs_combined": round(statistics.mean(all_obs_c), 4) if all_obs_c else 0,
        "note": "All results from jugeo CLI via subprocess",
    }
    results["summary"] = summary

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Total pairs:        {summary['total_pairs']}")
    print(f"  Total modules:      {summary['total_modules']}")
    print(f"  Convergence rate:   {summary['convergence_rate']:.1%}")
    print(f"  Mean obs (A):       {summary['mean_obs_a']:.2f}")
    print(f"  Mean obs (B):       {summary['mean_obs_b']:.2f}")
    print(f"  Mean obs (combined):{summary['mean_obs_combined']:.2f}")
    print()
    print("  LITERATURE BASELINES (not measured by this script):")
    for key, bl in LITERATURE_BASELINES.items():
        print(f"    {key}: {bl['description']}")
        print(f"      cite: {bl['cite']}")

    out = os.path.join(os.path.dirname(__file__), "results_paper08.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
