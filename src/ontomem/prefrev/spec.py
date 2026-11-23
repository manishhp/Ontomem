"""What PrefRev talks about: concept pools, hierarchy pairs, traps, paraphrases, coverage concepts.

Everything here is *benchmark content*, authored separately from the extractor's cue
lists and the ontology's labels:

* ``PARAPHRASES`` are surface forms that are deliberately NOT ontology labels, so the
  concept mapper has to generalise (some will map wrongly - that is measured).
* ``EXTERNAL`` concepts are NOT in the shipped ontology (coverage split, ~15% of
  statements). Gold knows where they belong; the system does not.
* ``TRAPS`` are pairs of different slots whose surface forms overlap, built to bait
  false merges.
"""

from __future__ import annotations

# Concepts used for ordinary statements, per domain (mid/leaf level - people rarely
# state preferences about "Food" as a whole).
POOLS: dict[str, list[str]] = {
    "Food": [
        "SpicyFood", "Seafood", "Sushi", "Shrimp", "Salmon", "Crab", "Meat", "RedMeat", "Poultry", "ChickenWings",
        "Paneer", "Tofu", "Cheese", "Mushroom", "Broccoli", "Eggplant", "Okra", "Mango", "Durian", "Rice",
        "Pasta", "Pizza", "Burger", "FrenchFries", "FriedFood", "StreetFood", "Salad", "Dessert", "IceCream",
        "Chocolate", "IndianSweets", "SouthIndianCuisine", "NorthIndianCuisine", "Curry", "ItalianCuisine",
        "JapaneseCuisine", "Ramen", "ChineseCuisine", "DimSum", "MexicanCuisine", "ThaiCuisine",
        "KoreanCuisine", "MediterraneanCuisine", "Coffee", "Tea", "Beer", "Wine", "Cocktail", "SoftDrink",
        "Nuts", "Dairy", "Gluten", "FastFood", "VeganFood",
    ],
    "Travel": [
        "Paris", "Rome", "Venice", "Barcelona", "London", "Switzerland", "Tokyo", "Kyoto", "Bangkok", "Phuket",
        "Goa", "Kerala", "Rajasthan", "Ladakh", "Himachal", "Bali", "Singapore", "Dubai", "NewYork", "Peru",
        "BeachHoliday", "MountainTrip", "CityBreak", "Trekking", "RoadTrip", "Cruise", "AdventureTravel",
        "LuxuryTravel", "BudgetTravel", "Hostel", "Homestay", "Camping", "LuxuryHotel", "RedEyeFlight",
        "LayoverFlight", "Train", "TravelCrowd", "Museum", "Nightlife",
    ],
    "Screen": [
        "ActionMovie", "ComedyMovie", "HorrorMovie", "ThrillerMovie", "DramaMovie", "SciFiMovie", "AnimatedMovie",
        "DocumentaryFilm", "RomanceMovie", "BollywoodMovie", "KannadaMovie", "SuperheroMovie", "Sitcom",
        "RealityTV", "CrimeSeries", "Anime", "KDrama", "Cricket", "Football", "Formula1",
    ],
    "Music": [
        "Genre_Rock", "Metal", "IndieRock", "Genre_Pop", "Genre_HipHop", "Genre_Electronic", "Genre_Jazz",
        "Genre_Classical", "IndianClassical", "Genre_Bollywood", "Genre_Folk", "Genre_LoFi", "LiveMusic", "Podcast",
    ],
    "Books": [
        "Fantasy_Book", "SciFi_Book", "Mystery_Book", "Romance_Book", "Horror_Book", "LiteraryFiction",
        "Thriller_Book", "Biography", "SelfHelp", "History_Book", "Science_Book", "Comics", "Poetry", "Audiobook",
    ],
    "Shopping": [
        "Iphone", "AndroidPhone", "Laptop", "Headphones", "SmartWatch", "Camera", "GamingConsole", "Formalwear",
        "Casualwear", "EthnicWear", "Sportswear", "FastFashion", "Sneakers", "Heels", "LuxuryBrands",
        "SustainableProducts", "SecondHand", "OnlineShopping", "Cosmetics",
    ],
}

DOMAIN_WEIGHTS = {"Food": 0.38, "Travel": 0.2, "Screen": 0.15, "Music": 0.1, "Books": 0.08, "Shopping": 0.09}

# (broad, narrow) pairs for exception / broad-override cases
HIERARCHY_PAIRS: list[tuple[str, str]] = [
    ("Seafood", "Sushi"), ("Seafood", "Shrimp"), ("Seafood", "Salmon"), ("Seafood", "Crab"),
    ("Shellfish", "Shrimp"), ("Fish", "Salmon"), ("Meat", "Poultry"), ("Meat", "ChickenWings"),
    ("Meat", "RedMeat"), ("FastFood", "Pizza"), ("FastFood", "Burger"), ("FriedFood", "ChickenWings"),
    ("Dairy", "Cheese"), ("Dairy", "IceCream"), ("Dessert", "Chocolate"), ("Dessert", "IceCream"),
    ("Alcohol", "Wine"), ("Alcohol", "Beer"), ("IndianCuisine", "SouthIndianCuisine"),
    ("IndianCuisine", "Curry"), ("JapaneseCuisine", "Ramen"), ("JapaneseCuisine", "Sushi"),
    ("ChineseCuisine", "DimSum"), ("Vegetables", "Mushroom"), ("Vegetables", "Broccoli"),
    ("Europe", "Paris"), ("Europe", "Venice"), ("Asia", "Tokyo"), ("Asia", "Bali"),
    ("IndiaDestinations", "Goa"), ("IndiaDestinations", "Ladakh"), ("Hotel", "LuxuryHotel"),
    ("Flight", "RedEyeFlight"), ("Flight", "LayoverFlight"), ("TVShow", "RealityTV"), ("TVShow", "KDrama"),
    ("Movie", "HorrorMovie"), ("Movie", "AnimatedMovie"), ("Genre_Rock", "Metal"), ("Genre_Rock", "IndieRock"),
    ("Genre_Classical", "IndianClassical"), ("Fiction", "Fantasy_Book"), ("NonFiction", "SelfHelp"),
    ("Smartphone", "Iphone"), ("Clothing", "FastFashion"), ("Footwear", "Heels"),
]

# Pairs of DIFFERENT slots whose surfaces overlap - bait for false merges.
TRAPS: list[tuple[tuple[str, str], tuple[str, str]]] = [
    (("SpicyFood", "spicy food"), ("ThrillerMovie", "spicy thrillers")),
    (("ThaiCuisine", "thai food"), ("Thailand", "thai islands")),
    (("JapaneseCuisine", "japanese food"), ("Anime", "japanese animation")),
    (("ItalianCuisine", "italian food"), ("Italy", "italian trip")),
    (("KoreanCuisine", "korean food"), ("KDrama", "korean dramas")),
    (("IndianCuisine", "indian food"), ("IndianClassical", "indian classical music")),
    (("HorrorMovie", "horror movies"), ("Horror_Book", "horror novels")),
    (("SciFiMovie", "sci-fi movies"), ("SciFi_Book", "sci-fi books")),
    (("FastFood", "fast food"), ("FastFashion", "fast fashion")),
    (("StreetFood", "street food"), ("Shopping_Travel", "street markets")),
    (("Genre_Pop", "pop music"), ("Science_Book", "pop science")),
    (("LiveMusic", "live music"), ("Sports_Viewing", "live sports")),
    (("ThrillerMovie", "crime thrillers"), ("Mystery_Book", "crime novels")),
    (("Coffee", "coffee"), ("Tea", "tea")),
    (("LuxuryHotel", "luxury hotels"), ("LuxuryBrands", "luxury brands")),
    (("BudgetTravel", "budget travel"), ("BudgetHotel", "budget hotels")),
]

# Surface forms that are NOT ontology labels (held-out paraphrases).
PARAPHRASES: dict[str, list[str]] = {
    "SpicyFood": ["chilli-heavy dishes", "food with a lot of heat", "fiery curries", "really hot dishes", "chilli-laden food"],
    "Seafood": ["anything from the sea", "sea creatures", "ocean food", "fishy stuff"],
    "Sushi": ["nigiri sets", "sashimi platters", "salmon rolls"],
    "Shrimp": ["prawn dishes", "garlic prawns", "tiger prawns"],
    "Meat": ["meaty dishes", "anything with meat in it", "non-veg food"],
    "RedMeat": ["beef dishes", "mutton curry", "pork ribs"],
    "Poultry": ["chicken dishes", "grilled chicken", "roast chicken"],
    "Coffee": ["my morning espresso", "cold brew", "flat whites"],
    "Tea": ["a cup of chai", "herbal tea", "black tea"],
    "Dessert": ["sweet treats after dinner", "puddings and desserts"],
    "FriedFood": ["deep-fried snacks", "fried stuff", "anything deep fried"],
    "Pizza": ["thin-crust pizza", "pizza slices", "wood-fired pizza"],
    "Mushroom": ["mushroom dishes", "anything with mushrooms", "wild mushroom dishes"],
    "Cheese": ["cheesy food", "aged cheeses"],
    "Paris": ["paris in spring", "the french capital"],
    "Goa": ["north goa", "the goa beaches"],
    "BeachHoliday": ["lazy beach days", "sun and sand holidays"],
    "MountainTrip": ["the hills", "mountain getaways"],
    "TravelCrowd": ["crowded tourist spots", "packed tourist places"],
    "RedEyeFlight": ["overnight red-eyes", "midnight flights"],
    "HorrorMovie": ["slasher flicks", "scary films", "ghost movies"],
    "ComedyMovie": ["funny films", "silly comedies"],
    "RealityTV": ["reality shows on tv", "dating shows"],
    "Genre_Jazz": ["jazz records", "bebop"],
    "Metal": ["heavy guitar music", "death metal"],
    "SelfHelp": ["self-improvement books", "books about better habits"],
    "FastFashion": ["cheap trendy clothes", "throwaway fashion"],
    "Iphone": ["apple's phones", "the new iphone"],
}

# Concepts NOT in the shipped ontology (coverage split). (id, surface, parent in the ontology)
EXTERNAL: list[tuple[str, str, str]] = [
    ("Jackfruit", "jackfruit", "Fruit"),
    ("VietnameseCuisine", "vietnamese food", "Cuisine"),
    ("Kombucha", "kombucha", "Beverage"),
    ("Matcha", "matcha", "Tea"),
    ("Tiramisu", "tiramisu", "Dessert"),
    ("Avocado", "avocado toast", "Fruit"),
    ("BubbleTea", "bubble tea", "Beverage"),
    ("Quinoa", "quinoa", "Grains"),
    ("Iceland", "iceland", "Europe"),
    ("Vietnam", "vietnam", "Asia"),
    ("Hampi", "hampi", "IndiaDestinations"),
    ("Meghalaya", "meghalaya", "IndiaDestinations"),
    ("Safari", "wildlife safaris", "TravelStyle"),
    ("Western", "westerns", "Movie"),
    ("StandUp", "stand-up comedy specials", "TVShow"),
    ("Afrobeats", "afrobeats", "Music"),
    ("Synthwave", "synthwave", "Genre_Electronic"),
    ("Qawwali", "qawwali", "Music"),
    ("CosyMystery", "cosy mysteries", "Mystery_Book"),
    ("TrueCrimeBook", "true crime books", "NonFiction"),
    ("MechanicalKeyboard", "mechanical keyboards", "Electronics"),
    ("Vinyl", "vinyl records", "Shopping"),
    ("Perfume", "perfume", "Cosmetics"),
]

FOOD_ONLY = {"Food"}
CONTEXTS_FOR = {
    "Food": ["work", "home", "travel", "family", "social"],
    "Travel": ["family", "work"],
    "Screen": ["family", "social"],
    "Music": ["work", "social"],
    "Books": ["travel"],
    "Shopping": ["work"],
}
THIRD_PARTY_ROLES = ["partner", "child", "parent", "friend"]
