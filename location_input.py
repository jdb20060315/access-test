"""把指南针显示的 GPS 经纬度整理成点位接口使用的百度经纬度。"""
import math
import re


def degrees(value, limit):
    if isinstance(value, bool):
        raise ValueError('invalid_coordinate')
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str) and len(value) <= 48:
        value = value.strip().upper().replace('北纬', 'N').replace('南纬', 'S').replace('东经', 'E').replace('西经', 'W')
        direction = re.findall('[NSEW]', value)
        if len(direction) > 1:
            raise ValueError('invalid_coordinate')
        value = re.sub('[NSEW]', '', value).strip()
        clean_dec = re.sub(r'[°度]$', '', value).strip()
        if re.fullmatch(r'[+-]?\d{1,3}(?:\.\d{1,8})?', clean_dec):
            result = float(clean_dec)
        else:
            parts = re.fullmatch(r'(\d{1,3})\s*[°度]\s*(\d{1,2})(?:\s*[′\'分]\s*(\d{1,2}(?:\.\d{1,4})?)\s*(?:[″"秒])?)?\s*(?:[′\'分])?', value)
            if not parts:
                raise ValueError('invalid_coordinate')
            whole, minute, second = parts.groups()
            if int(minute) >= 60 or second and float(second) >= 60:
                raise ValueError('invalid_coordinate')
            result = int(whole) + int(minute) / 60 + float(second or 0) / 3600
        if direction and direction[0] in 'SW':
            result = -result
    else:
        raise ValueError('invalid_coordinate')
    if not math.isfinite(result) or not -limit <= result <= limit:
        raise ValueError('invalid_coordinate')
    return result


def location(city, latitude, longitude):
    if not isinstance(city, str) or not re.fullmatch(r'[\u4e00-\u9fffA-Za-z ·-]{2,32}', city.strip()):
        raise ValueError('invalid_city')
    lat, lon = degrees(latitude, 90), degrees(longitude, 180)
    if not 18 <= lat <= 54 or not 73 <= lon <= 135:
        raise ValueError('location_outside_supported_area')
    # ponytail: 无外部地图密钥时采用本地近似换算；学校点位由上游实时接口决定。
    x, y = lon - 105, lat - 35
    delta_lat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    delta_lat += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    delta_lat += (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi))*2/3
    delta_lat += (160*math.sin(y/12*math.pi) + 320*math.sin(y*math.pi/30))*2/3
    delta_lon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    delta_lon += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    delta_lon += (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi))*2/3
    delta_lon += (150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi))*2/3
    radians = lat / 180 * math.pi
    magic = 1 - .00669342162296594323 * math.sin(radians)**2
    gcj_lat = lat + delta_lat * 180 / ((6335552.717000426 / (magic*math.sqrt(magic))) * math.pi)
    gcj_lon = lon + delta_lon * 180 / ((6378245 / math.sqrt(magic) * math.cos(radians)) * math.pi)
    z = math.hypot(gcj_lon, gcj_lat) + .00002*math.sin(gcj_lat*math.pi*3000/180)
    theta = math.atan2(gcj_lat, gcj_lon) + .000003*math.cos(gcj_lon*math.pi*3000/180)
    return {'city': city.strip(), 'anchor_lat': round(z*math.sin(theta)+.006, 6),
            'anchor_lon': round(z*math.cos(theta)+.0065, 6)}
