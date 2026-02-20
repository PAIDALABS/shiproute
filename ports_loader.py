from __future__ import annotations
from typing import Optional

PORTS = [
    # East Asia
    {"id": "cnsha", "name": "Shanghai", "country": "China", "locode": "CNSHA", "lat": 31.23, "lon": 121.47},
    {"id": "cnngb", "name": "Ningbo-Zhoushan", "country": "China", "locode": "CNNGB", "lat": 29.87, "lon": 121.55},
    {"id": "cnszx", "name": "Shenzhen", "country": "China", "locode": "CNSZX", "lat": 22.54, "lon": 114.06},
    {"id": "cngzu", "name": "Guangzhou", "country": "China", "locode": "CNGZU", "lat": 23.11, "lon": 113.25},
    {"id": "cnqin", "name": "Qingdao", "country": "China", "locode": "CNQIN", "lat": 36.07, "lon": 120.38},
    {"id": "cntjn", "name": "Tianjin", "country": "China", "locode": "CNTJN", "lat": 39.13, "lon": 117.72},
    {"id": "cnxmn", "name": "Xiamen", "country": "China", "locode": "CNXMN", "lat": 24.48, "lon": 118.07},
    {"id": "cndlc", "name": "Dalian", "country": "China", "locode": "CNDLC", "lat": 38.91, "lon": 121.62},
    {"id": "cncan", "name": "Guangzhou Nansha", "country": "China", "locode": "CNCAN", "lat": 22.75, "lon": 113.58},
    {"id": "cnlyg", "name": "Lianyungang", "country": "China", "locode": "CNLYG", "lat": 34.75, "lon": 119.44},
    {"id": "krpus", "name": "Busan", "country": "South Korea", "locode": "KRPUS", "lat": 35.10, "lon": 129.04},
    {"id": "krinc", "name": "Incheon", "country": "South Korea", "locode": "KRINC", "lat": 37.46, "lon": 126.62},
    {"id": "jposa", "name": "Osaka", "country": "Japan", "locode": "JPOSA", "lat": 34.65, "lon": 135.43},
    {"id": "jptyo", "name": "Tokyo", "country": "Japan", "locode": "JPTYO", "lat": 35.62, "lon": 139.76},
    {"id": "jpngo", "name": "Nagoya", "country": "Japan", "locode": "JPNGO", "lat": 35.07, "lon": 136.88},
    {"id": "jpyok", "name": "Yokohama", "country": "Japan", "locode": "JPYOK", "lat": 35.44, "lon": 139.64},
    {"id": "jpkob", "name": "Kobe", "country": "Japan", "locode": "JPKOB", "lat": 34.69, "lon": 135.19},
    {"id": "twkhh", "name": "Kaohsiung", "country": "Taiwan", "locode": "TWKHH", "lat": 22.62, "lon": 120.27},
    {"id": "twtpe", "name": "Taipei (Keelung)", "country": "Taiwan", "locode": "TWTPE", "lat": 25.15, "lon": 121.74},
    {"id": "hkhkg", "name": "Hong Kong", "country": "Hong Kong", "locode": "HKHKG", "lat": 22.30, "lon": 114.17},
    # Southeast Asia
    {"id": "sgsin", "name": "Singapore", "country": "Singapore", "locode": "SGSIN", "lat": 1.29, "lon": 103.85},
    {"id": "mypkg", "name": "Port Klang", "country": "Malaysia", "locode": "MYPKG", "lat": 3.00, "lon": 101.40},
    {"id": "mypen", "name": "Penang", "country": "Malaysia", "locode": "MYPEN", "lat": 5.42, "lon": 100.33},
    {"id": "idtpp", "name": "Tanjung Priok (Jakarta)", "country": "Indonesia", "locode": "IDTPP", "lat": -6.10, "lon": 106.88},
    {"id": "idplm", "name": "Palembang", "country": "Indonesia", "locode": "IDPLM", "lat": -2.99, "lon": 104.76},
    {"id": "idmak", "name": "Makassar", "country": "Indonesia", "locode": "IDMAK", "lat": -5.14, "lon": 119.42},
    {"id": "vhsgn", "name": "Ho Chi Minh City", "country": "Vietnam", "locode": "VNSGN", "lat": 10.79, "lon": 106.72},
    {"id": "vnhan", "name": "Haiphong", "country": "Vietnam", "locode": "VNHAN", "lat": 20.87, "lon": 106.68},
    {"id": "thbkk", "name": "Bangkok (Laem Chabang)", "country": "Thailand", "locode": "THLCH", "lat": 13.08, "lon": 100.88},
    {"id": "phmnl", "name": "Manila", "country": "Philippines", "locode": "PHMNL", "lat": 14.59, "lon": 120.97},
    {"id": "mmrgn", "name": "Yangon", "country": "Myanmar", "locode": "MMRGN", "lat": 16.78, "lon": 96.16},
    # South Asia
    {"id": "inbom", "name": "Mumbai (JNPT)", "country": "India", "locode": "INBOM", "lat": 18.95, "lon": 72.95},
    {"id": "inmaa", "name": "Chennai", "country": "India", "locode": "INMAA", "lat": 13.10, "lon": 80.30},
    {"id": "inccu", "name": "Kolkata", "country": "India", "locode": "INCCU", "lat": 22.57, "lon": 88.37},
    {"id": "inkoc", "name": "Kochi", "country": "India", "locode": "INKOC", "lat": 9.97, "lon": 76.27},
    {"id": "inpav", "name": "Mundra", "country": "India", "locode": "INPAV", "lat": 22.84, "lon": 69.72},
    {"id": "lkcmb", "name": "Colombo", "country": "Sri Lanka", "locode": "LKCMB", "lat": 6.95, "lon": 79.85},
    {"id": "bdcgp", "name": "Chittagong", "country": "Bangladesh", "locode": "BDCGP", "lat": 22.33, "lon": 91.83},
    {"id": "pkkar", "name": "Karachi", "country": "Pakistan", "locode": "PKKAR", "lat": 24.85, "lon": 67.01},
    # Middle East
    {"id": "aedxb", "name": "Dubai (Jebel Ali)", "country": "UAE", "locode": "AEJEA", "lat": 24.98, "lon": 55.06},
    {"id": "aeauh", "name": "Abu Dhabi", "country": "UAE", "locode": "AEAUH", "lat": 24.47, "lon": 54.37},
    {"id": "sajtb", "name": "Jeddah", "country": "Saudi Arabia", "locode": "SAJTB", "lat": 21.49, "lon": 39.17},
    {"id": "sadmm", "name": "Dammam", "country": "Saudi Arabia", "locode": "SADMM", "lat": 26.43, "lon": 50.10},
    {"id": "iqbsr", "name": "Basra (Umm Qasr)", "country": "Iraq", "locode": "IQUMQ", "lat": 30.03, "lon": 47.96},
    {"id": "kwkwi", "name": "Kuwait", "country": "Kuwait", "locode": "KWKWI", "lat": 29.38, "lon": 47.97},
    {"id": "ombct", "name": "Muscat (Sohar)", "country": "Oman", "locode": "OMSOH", "lat": 24.34, "lon": 56.64},
    {"id": "irbnd", "name": "Bandar Abbas", "country": "Iran", "locode": "IRBND", "lat": 27.18, "lon": 56.27},
    # Red Sea / East Africa
    {"id": "djjib", "name": "Djibouti", "country": "Djibouti", "locode": "DJJIB", "lat": 11.60, "lon": 43.14},
    {"id": "somgq", "name": "Mogadishu", "country": "Somalia", "locode": "SOMGQ", "lat": 2.04, "lon": 45.34},
    {"id": "etmop", "name": "Mombasa", "country": "Kenya", "locode": "KEMBA", "lat": -4.05, "lon": 39.67},
    {"id": "tzdar", "name": "Dar es Salaam", "country": "Tanzania", "locode": "TZDAR", "lat": -6.82, "lon": 39.29},
    {"id": "mzmpm", "name": "Maputo", "country": "Mozambique", "locode": "MZMPM", "lat": -25.97, "lon": 32.57},
    {"id": "zadur", "name": "Durban", "country": "South Africa", "locode": "ZADUR", "lat": -29.87, "lon": 31.03},
    {"id": "zacpt", "name": "Cape Town", "country": "South Africa", "locode": "ZACPT", "lat": -33.92, "lon": 18.42},
    # West Africa
    {"id": "nglos", "name": "Lagos (Apapa)", "country": "Nigeria", "locode": "NGLOS", "lat": 6.45, "lon": 3.38},
    {"id": "ghtem", "name": "Tema", "country": "Ghana", "locode": "GHTEM", "lat": 5.62, "lon": -0.01},
    {"id": "ciabj", "name": "Abidjan", "country": "Côte d'Ivoire", "locode": "CIABJ", "lat": 5.35, "lon": -4.00},
    {"id": "sndkr", "name": "Dakar", "country": "Senegal", "locode": "SNDKR", "lat": 14.69, "lon": -17.44},
    {"id": "aolad", "name": "Luanda", "country": "Angola", "locode": "AOLAD", "lat": -8.84, "lon": 13.23},
    {"id": "cmdla", "name": "Douala", "country": "Cameroon", "locode": "CMDLA", "lat": 4.05, "lon": 9.70},
    # Mediterranean
    {"id": "egpsd", "name": "Port Said", "country": "Egypt", "locode": "EGPSD", "lat": 31.26, "lon": 32.30},
    {"id": "egaly", "name": "Alexandria", "country": "Egypt", "locode": "EGALY", "lat": 31.20, "lon": 29.92},
    {"id": "ilhfa", "name": "Haifa", "country": "Israel", "locode": "ILHFA", "lat": 32.82, "lon": 34.99},
    {"id": "joaqj", "name": "Aqaba", "country": "Jordan", "locode": "JOAQJ", "lat": 29.52, "lon": 35.00},
    {"id": "trizm", "name": "Izmir", "country": "Turkey", "locode": "TRIZM", "lat": 38.42, "lon": 27.14},
    {"id": "trist", "name": "Istanbul", "country": "Turkey", "locode": "TRIST", "lat": 41.01, "lon": 28.96},
    {"id": "grath", "name": "Piraeus", "country": "Greece", "locode": "GRATH", "lat": 37.94, "lon": 23.64},
    {"id": "itgoa", "name": "Genoa", "country": "Italy", "locode": "ITGOA", "lat": 44.41, "lon": 8.93},
    {"id": "itliv", "name": "Livorno", "country": "Italy", "locode": "ITLIV", "lat": 43.55, "lon": 10.31},
    {"id": "itgit", "name": "Gioia Tauro", "country": "Italy", "locode": "ITGIT", "lat": 38.43, "lon": 15.90},
    {"id": "itvce", "name": "Venice", "country": "Italy", "locode": "ITVCE", "lat": 45.44, "lon": 12.32},
    {"id": "ittrs", "name": "Trieste", "country": "Italy", "locode": "ITTRS", "lat": 45.65, "lon": 13.77},
    {"id": "esagp", "name": "Algeciras", "country": "Spain", "locode": "ESAGP", "lat": 36.13, "lon": -5.45},
    {"id": "esbcn", "name": "Barcelona", "country": "Spain", "locode": "ESBCN", "lat": 41.35, "lon": 2.18},
    {"id": "esvlc", "name": "Valencia", "country": "Spain", "locode": "ESVLC", "lat": 39.46, "lon": -0.32},
    {"id": "maptm", "name": "Tanger Med", "country": "Morocco", "locode": "MAPTM", "lat": 35.88, "lon": -5.51},
    {"id": "frmrs", "name": "Marseille", "country": "France", "locode": "FRMRS", "lat": 43.30, "lon": 5.37},
    {"id": "mtmar", "name": "Marsaxlokk", "country": "Malta", "locode": "MTMAR", "lat": 35.83, "lon": 14.54},
    # Northern Europe
    {"id": "nlrtm", "name": "Rotterdam", "country": "Netherlands", "locode": "NLRTM", "lat": 51.92, "lon": 4.48},
    {"id": "deham", "name": "Hamburg", "country": "Germany", "locode": "DEHAM", "lat": 53.54, "lon": 9.99},
    {"id": "beanr", "name": "Antwerp", "country": "Belgium", "locode": "BEANR", "lat": 51.23, "lon": 4.42},
    {"id": "gbfxt", "name": "Felixstowe", "country": "United Kingdom", "locode": "GBFXT", "lat": 51.96, "lon": 1.35},
    {"id": "gbliv", "name": "Liverpool", "country": "United Kingdom", "locode": "GBLIV", "lat": 53.40, "lon": -3.00},
    {"id": "gbsou", "name": "Southampton", "country": "United Kingdom", "locode": "GBSOU", "lat": 50.90, "lon": -1.40},
    {"id": "gblon", "name": "London (Tilbury)", "country": "United Kingdom", "locode": "GBLON", "lat": 51.46, "lon": 0.36},
    {"id": "frleh", "name": "Le Havre", "country": "France", "locode": "FRLEH", "lat": 49.49, "lon": 0.11},
    {"id": "dkaar", "name": "Aarhus", "country": "Denmark", "locode": "DKAAR", "lat": 56.15, "lon": 10.22},
    {"id": "dkpdc", "name": "Copenhagen", "country": "Denmark", "locode": "DKCPH", "lat": 55.68, "lon": 12.57},
    {"id": "segot", "name": "Gothenburg", "country": "Sweden", "locode": "SEGOT", "lat": 57.71, "lon": 11.97},
    {"id": "fihlk", "name": "Helsinki", "country": "Finland", "locode": "FIHLK", "lat": 60.16, "lon": 24.94},
    {"id": "nosvg", "name": "Stavanger", "country": "Norway", "locode": "NOSVG", "lat": 58.97, "lon": 5.73},
    {"id": "noaes", "name": "Alesund", "country": "Norway", "locode": "NOAES", "lat": 62.47, "lon": 6.15},
    {"id": "plgdy", "name": "Gdynia", "country": "Poland", "locode": "PLGDY", "lat": 54.52, "lon": 18.55},
    {"id": "eetel", "name": "Tallinn", "country": "Estonia", "locode": "EETEL", "lat": 59.44, "lon": 24.75},
    {"id": "lvrix", "name": "Riga", "country": "Latvia", "locode": "LVRIX", "lat": 56.95, "lon": 24.11},
    # Americas — North Atlantic
    {"id": "ussav", "name": "Savannah", "country": "United States", "locode": "USSAV", "lat": 32.08, "lon": -81.10},
    {"id": "usnyc", "name": "New York", "country": "United States", "locode": "USNYC", "lat": 40.67, "lon": -74.01},
    {"id": "usbos", "name": "Boston", "country": "United States", "locode": "USBOS", "lat": 42.36, "lon": -71.04},
    {"id": "usblt", "name": "Baltimore", "country": "United States", "locode": "USBLT", "lat": 39.27, "lon": -76.58},
    {"id": "usorf", "name": "Norfolk", "country": "United States", "locode": "USORF", "lat": 36.84, "lon": -76.30},
    {"id": "usjax", "name": "Jacksonville", "country": "United States", "locode": "USJAX", "lat": 30.33, "lon": -81.66},
    {"id": "ushou", "name": "Houston", "country": "United States", "locode": "USHOU", "lat": 29.73, "lon": -95.27},
    {"id": "usnol", "name": "New Orleans", "country": "United States", "locode": "USNOL", "lat": 29.94, "lon": -90.07},
    {"id": "uschi", "name": "Chicago", "country": "United States", "locode": "USCHI", "lat": 41.85, "lon": -87.65},
    # Americas — West Coast / Pacific
    {"id": "uslax", "name": "Los Angeles / Long Beach", "country": "United States", "locode": "USLAX", "lat": 33.73, "lon": -118.27},
    {"id": "ussea", "name": "Seattle / Tacoma", "country": "United States", "locode": "USSEA", "lat": 47.60, "lon": -122.33},
    {"id": "usoak", "name": "Oakland", "country": "United States", "locode": "USOAK", "lat": 37.80, "lon": -122.27},
    {"id": "cahal", "name": "Halifax", "country": "Canada", "locode": "CAHAL", "lat": 44.65, "lon": -63.58},
    {"id": "cavnc", "name": "Vancouver", "country": "Canada", "locode": "CAVAN", "lat": 49.29, "lon": -123.12},
    {"id": "camtr", "name": "Montreal", "country": "Canada", "locode": "CAMTR", "lat": 45.50, "lon": -73.56},
    {"id": "mxver", "name": "Veracruz", "country": "Mexico", "locode": "MXVER", "lat": 19.20, "lon": -96.13},
    {"id": "mxzlo", "name": "Manzanillo", "country": "Mexico", "locode": "MXZLO", "lat": 19.05, "lon": -104.32},
    # Caribbean / Central America
    {"id": "pamit", "name": "Manzanillo (Panama)", "country": "Panama", "locode": "PAMIT", "lat": 9.36, "lon": -79.83},
    {"id": "paonx", "name": "Colon (Panama)", "country": "Panama", "locode": "PAONX", "lat": 9.35, "lon": -79.90},
    {"id": "cobun", "name": "Buenaventura", "country": "Colombia", "locode": "COBUN", "lat": 3.88, "lon": -77.02},
    {"id": "cubhv", "name": "Havana", "country": "Cuba", "locode": "CUBHV", "lat": 23.14, "lon": -82.37},
    {"id": "jmkin", "name": "Kingston", "country": "Jamaica", "locode": "JMKIN", "lat": 17.98, "lon": -76.79},
    # South America
    {"id": "brssz", "name": "Santos", "country": "Brazil", "locode": "BRSSZ", "lat": -23.96, "lon": -46.33},
    {"id": "brrec", "name": "Recife", "country": "Brazil", "locode": "BRREC", "lat": -8.06, "lon": -34.87},
    {"id": "brrig", "name": "Rio de Janeiro", "country": "Brazil", "locode": "BRREC", "lat": -22.90, "lon": -43.17},
    {"id": "arbue", "name": "Buenos Aires", "country": "Argentina", "locode": "ARBUE", "lat": -34.61, "lon": -58.37},
    {"id": "clvap", "name": "Valparaiso", "country": "Chile", "locode": "CLVAP", "lat": -33.04, "lon": -71.62},
    {"id": "pecll", "name": "Callao (Lima)", "country": "Peru", "locode": "PECLL", "lat": -12.05, "lon": -77.14},
    {"id": "uymvd", "name": "Montevideo", "country": "Uruguay", "locode": "UYMVD", "lat": -34.91, "lon": -56.17},
    # Oceania
    {"id": "ausyd", "name": "Sydney", "country": "Australia", "locode": "AUSYD", "lat": -33.86, "lon": 151.21},
    {"id": "aumel", "name": "Melbourne", "country": "Australia", "locode": "AUMEL", "lat": -37.82, "lon": 144.97},
    {"id": "aubne", "name": "Brisbane", "country": "Australia", "locode": "AUBNE", "lat": -27.47, "lon": 153.02},
    {"id": "aufre", "name": "Fremantle (Perth)", "country": "Australia", "locode": "AUFRE", "lat": -32.05, "lon": 115.75},
    {"id": "auadl", "name": "Adelaide", "country": "Australia", "locode": "AUADL", "lat": -34.92, "lon": 138.60},
    {"id": "nzakl", "name": "Auckland", "country": "New Zealand", "locode": "NZAKL", "lat": -36.84, "lon": 174.76},
    {"id": "fjsuv", "name": "Suva", "country": "Fiji", "locode": "FJSUV", "lat": -18.14, "lon": 178.44},
    {"id": "phmaa", "name": "Cebu", "country": "Philippines", "locode": "PHCEB", "lat": 10.31, "lon": 123.89},
    # Other important hubs
    {"id": "zacpe", "name": "Port Elizabeth", "country": "South Africa", "locode": "ZAPLZ", "lat": -33.96, "lon": 25.63},
    {"id": "muplu", "name": "Port Louis", "country": "Mauritius", "locode": "MUPLU", "lat": -20.16, "lon": 57.50},
    {"id": "rereo", "name": "Reunion", "country": "France", "locode": "REREO", "lat": -20.93, "lon": 55.47},
    {"id": "mgtnr", "name": "Toamasina", "country": "Madagascar", "locode": "MGTNR", "lat": -18.15, "lon": 49.40},
    {"id": "ytmam", "name": "Mayotte", "country": "France", "locode": "YTMAM", "lat": -12.78, "lon": 45.23},
    {"id": "scpov", "name": "Victoria (Seychelles)", "country": "Seychelles", "locode": "SCPOV", "lat": -4.62, "lon": 55.46},
]


class PortsLoader:
    def __init__(self):
        self._ports = PORTS
        self._by_id = {p["id"]: p for p in PORTS}
        self._by_locode = {p["locode"].upper(): p for p in PORTS}

    def search(self, q: str, limit: int = 10) -> list[dict]:
        q = q.strip()
        if not q:
            return []
        ql = q.lower()
        exact_prefix: list[dict] = []
        contains: list[dict] = []
        for port in self._ports:
            name_l = port["name"].lower()
            locode_l = port["locode"].lower()
            if name_l.startswith(ql) or locode_l.startswith(ql):
                exact_prefix.append(port)
            elif ql in name_l or ql in locode_l:
                contains.append(port)
        results = exact_prefix + contains
        return results[:limit]

    def get(self, port_id: str) -> Optional[dict]:
        port_id_clean = port_id.strip()
        # Try exact id
        p = self._by_id.get(port_id_clean.lower())
        if p:
            return p
        # Try locode
        p = self._by_locode.get(port_id_clean.upper())
        if p:
            return p
        return None
