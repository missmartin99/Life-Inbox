"""Seed 1,000 highly realistic, feature-rich notes into Life Inbox.

This script populates the database so that every advanced feature of Life Inbox
can 'flex its muscles':
1. People & Entities Knowledge Graph (recurring people, places, orgs + dossiers)
2. Smart Templates & Typed Cards (Recipes, Books, Contacts, Events)
3. Receipts & Monthly Expense Tracker (Food, Travel, Utilities, Shopping, Medical)
4. Leitner Spaced-Repetition Flashcards (Box 0-4 progression + due dates)
5. 'On This Day' Resurfacing (1 week, 1 month, 3 months, 1 year ago)
6. Actionable Tasks (Today, Overdue, Upcoming with time-aware phrases)
7. Privacy Shield (Confidential / sensitive flagged items)
8. Semantic Search & Natural Language QA (medical, tech, financial, culinary concepts)
"""

from __future__ import annotations

import json
import random
from datetime import datetime, date, timedelta, timezone

from sqlalchemy import delete, select
from app.database import (
    SessionLocal,
    InboxItemRecord,
    EntityRecord,
    ItemEntityRecord,
    FlashcardRecord,
    TaskRecord,
    TaskSourceItemRecord,
    get_or_create_default_owner_id,
)

# Anchor reference time
NOW = datetime(2026, 11, 12, 17, 30, 0, tzinfo=timezone.utc)
TODAY = NOW.date()

PEOPLE = [
    ("Dr. Mehta", "person"),
    ("Rohan", "person"),
    ("Priya Sharma", "person"),
    ("CA Amit Verma", "person"),
    ("Ananya", "person"),
    ("Chef Kapoor", "person"),
    ("Professor Iyer", "person"),
    ("Vikram Malhotra", "person"),
    ("Sunita Gupta", "person"),
    ("Rahul Verma", "person"),
    ("Kavita Reddy", "person"),
    ("Arjun Nair", "person"),
    ("Neha Kapoor", "person"),
]

PLACES = [
    ("Indiranagar", "place"),
    ("Gurgaon", "place"),
    ("Bangalore", "place"),
    ("Bandra", "place"),
    ("Mumbai", "place"),
    ("Jaipur", "place"),
    ("Goa", "place"),
    ("Delhi", "place"),
    ("Chennai", "place"),
    ("Hyderabad", "place"),
]

ORGANIZATIONS = [
    ("DeepMind", "organization"),
    ("Google", "organization"),
    ("HDFC Bank", "organization"),
    ("ICICI Bank", "organization"),
    ("AIIMS", "organization"),
    ("Apollo Pharmacy", "organization"),
    ("Blue Tokai", "organization"),
    ("Swiggy", "organization"),
    ("Zomato", "organization"),
    ("Flipkart", "organization"),
]

ENTITIES_DICT = {name: (name, etype) for name, etype in (PEOPLE + PLACES + ORGANIZATIONS)}


def random_past_date(days_back_min=1, days_back_max=360) -> datetime:
    days = random.randint(days_back_min, days_back_max)
    hours = random.randint(8, 22)
    minutes = random.randint(0, 59)
    return (NOW - timedelta(days=days)).replace(hour=hours, minute=minutes, second=0)


def generate_notes_data():
    items = []

    # -------------------------------------------------------------
    # 1. SPECIFIC "ON THIS DAY" RESURFACING ANCHORS (Exact milestones)
    # -------------------------------------------------------------
    exact_milestones = [
        # Exactly 7 days ago (2026-11-05)
        (7, "Reviewing Q4 architecture design with Priya Sharma at DeepMind: discussed moving from monolithic indexing to hybrid sparse-dense retrieval.", ["Priya Sharma", "DeepMind"]),
        (7, "Dr. Mehta clinic checkup in Indiranagar: blood pressure was 120/80. Continue regular morning brisk walks.", ["Dr. Mehta", "Indiranagar"]),
        (7, "Dinner at Saravana Bhavan in Chennai with Ananya: Rs. 840 on 05/11/2026 for Food.", ["Saravana Bhavan", "Ananya", "Chennai"], "receipt", {"vendor": "Saravana Bhavan", "amount": 840.0, "category": "Food & Dining", "date": "2026-11-05"}),
        
        # Exactly 31 days ago (2026-10-12, ~1 month ago)
        (31, "CA Amit Verma sent the provisional advance tax calculation for Q3. Need to reconcile bank statements from HDFC Bank and ICICI Bank before filing.", ["CA Amit Verma", "HDFC Bank", "ICICI Bank"]),
        (31, "Booked return flight to Goa for the winter long weekend with Rohan and Vikram Malhotra. Flight departs early morning from Bangalore.", ["Goa", "Rohan", "Vikram Malhotra", "Bangalore"]),
        (31, "Amazon order for wireless mechanical keyboard: ₹4,899 on 12/10/2026.", ["Amazon"], "receipt", {"vendor": "Amazon", "amount": 4899.0, "category": "Shopping", "date": "2026-10-12"}),

        # Exactly 92 days ago (~3 months ago, 2026-08-12)
        (92, "Chef Kapoor taught us the authentic slow-cook Dum Biryani technique in Hyderabad: seal the handi with whole wheat dough and simmer on low charcoal heat.", ["Chef Kapoor", "Hyderabad"], "recipe", {"title": "Hyderabadi Dum Biryani", "prep_time": "120 mins", "ingredients": ["basmati rice", "marinated mutton", "fried onions", "saffron milk", "ghee", "whole spices"], "servings": 6}),
        (92, "Professor Iyer recommended reading 'Designing Data-Intensive Applications' by Martin Kleppmann for consensus algorithms and partition fault tolerance.", ["Professor Iyer"], "book", {"title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann", "genre": "System Design", "status": "Reading"}),
        (92, "Coffee meeting at Blue Tokai Bandra with Sunita Gupta: Rs. 620 on 12/08/2026.", ["Blue Tokai", "Bandra", "Sunita Gupta"], "receipt", {"vendor": "Blue Tokai", "amount": 620.0, "category": "Food & Dining", "date": "2026-08-12"}),

        # Exactly 365 days ago (1 year ago, 2025-11-12)
        (365, "First day setting up Life Inbox prototype on Google Colab! Aiming to replace disorganized Apple Notes with an intelligent AI copilot.", ["Google"]),
        (365, "Annual health checkup at AIIMS Delhi: all baseline biomarkers nominal, advised to increase Vitamin D intake.", ["AIIMS", "Delhi"]),
        (365, "Dinner celebration at Toit Brewery Indiranagar with Rohan and Arjun Nair: Rs. 3,450 on 12/11/2025.", ["Toit Brewery", "Indiranagar", "Rohan", "Arjun Nair"], "receipt", {"vendor": "Toit Brewery", "amount": 3450.0, "category": "Food & Dining", "date": "2025-11-12"}),
    ]

    for item in exact_milestones:
        days_ago = item[0]
        text = item[1]
        ents = item[2]
        kind = item[3] if len(item) > 3 else None
        kind_fields = item[4] if len(item) > 4 else None
        dt = (NOW - timedelta(days=days_ago)).replace(hour=14, minute=0, second=0)
        items.append({
            "text": text,
            "created_at": dt.isoformat(),
            "entities": ents,
            "kind": kind,
            "kind_fields": kind_fields,
            "is_sensitive": False,
        })

    # -------------------------------------------------------------
    # 2. SMART CARDS: RECEIPTS & EXPENSES (100 items across categories)
    # -------------------------------------------------------------
    vendors_by_cat = {
        "Food & Dining": [
            ("Blue Tokai Coffee", 420.0), ("Saravana Bhavan", 680.0), ("Swiggy Gourmet Order", 950.0),
            ("Zomato Biryani Order", 1120.0), ("Toit Indiranagar", 2400.0), ("Third Wave Coffee", 380.0),
            ("Haldiram's Sweets", 750.0), ("Subway Lunch", 340.0), ("Bakehouse Focaccia", 560.0),
        ],
        "Travel & Fuel": [
            ("Uber Ride to Bangalore Airport", 1150.0), ("Uber Commute to Indiranagar", 320.0),
            ("Indian Oil Petrol", 2500.0), ("IndiGo Flight Ticket", 4850.0), ("Delhi Metro Smart Card", 500.0),
            ("Fastag Toll Recharge", 1000.0), ("Airport Shuttle", 250.0),
        ],
        "Utilities & Bills": [
            ("Airtel Xstream Fiber Broadband", 1179.0), ("BESCOM Electricity Bill", 2340.0),
            ("Tata Power Bill", 1890.0), ("Piped Natural Gas Bill", 640.0), ("Water Tanker Delivery", 850.0),
            ("Society Maintenance Fee", 4500.0),
        ],
        "Shopping & Electronics": [
            ("Amazon Ergonomic Mouse", 2299.0), ("Uniqlo Linen Shirt", 2990.0), ("Ikea Desk Organizer", 1490.0),
            ("Apple iCloud 200GB Storage", 219.0), ("Decathlon Gym Gear", 1850.0), ("Nykaa Skincare", 1250.0),
            ("Kindle E-book Purchase", 499.0), ("Crocs Footwear", 3495.0),
        ],
        "Healthcare & Pharmacy": [
            ("Apollo Pharmacy Prescription Medicines", 1680.0), ("Max Healthcare Consultation", 1200.0),
            ("Dental Scaling and Polishing", 2000.0), ("Thyrocare Full Body Blood Test", 2499.0),
            ("1mg Vitamin Supplements", 890.0),
        ],
    }

    receipt_count = 0
    for cat, vendor_list in vendors_by_cat.items():
        for i in range(20):
            vendor, base_amt = random.choice(vendor_list)
            multiplier = random.uniform(0.85, 1.35)
            amt = round(base_amt * multiplier, 2)
            dt = random_past_date(1, 350)
            date_str = dt.strftime("%Y-%m-%d")
            text = f"{cat} expense at {vendor}: Rs. {amt:.2f} on {date_str}."
            items.append({
                "text": text,
                "created_at": dt.isoformat(),
                "entities": [vendor] if vendor in ENTITIES_DICT else [],
                "kind": "receipt",
                "kind_fields": {
                    "vendor": vendor,
                    "amount": amt,
                    "date": date_str,
                    "category": cat,
                },
                "is_sensitive": False,
            })
            receipt_count += 1

    # -------------------------------------------------------------
    # 3. SMART CARDS: RECIPES (50 items)
    # -------------------------------------------------------------
    recipes_pool = [
        ("Masala Chai", "15 mins", ["water", "milk", "crushed ginger", "cardamom pods", "assam tea leaves", "brown sugar"], 2),
        ("Paneer Butter Masala", "35 mins", ["fresh paneer cubes", "butter", "tomato puree", "cashew paste", "kasuri methi", "heavy cream"], 4),
        ("Dal Tadka", "30 mins", ["toor dal", "yellow moong dal", "ghee", "cumin seeds", "garlic cloves", "hing", "green chilies"], 4),
        ("Pasta Aglio e Olio", "20 mins", ["spaghetti", "extra virgin olive oil", "sliced garlic", "red pepper flakes", "fresh parsley", "parmigiano reggiano"], 2),
        ("Sourdough Focaccia", "45 mins", ["active sourdough starter", "bread flour", "warm water", "olive oil", "flaky sea salt", "fresh rosemary"], 6),
        ("Thai Green Curry", "30 mins", ["coconut milk", "green curry paste", "tofu or chicken", "bamboo shoots", "thai basil", "fish sauce", "kaffir lime leaves"], 4),
        ("Classic Guacamole", "10 mins", ["ripe hass avocados", "finely diced red onion", "cilantro", "lime juice", "jalapeno", "kosher salt"], 4),
        ("Chocolate Chip Cookies", "25 mins", ["all-purpose flour", "unsalted butter", "dark brown sugar", "dark chocolate chips", "vanilla extract", "sea salt"], 12),
        ("Overnight Chia Oats", "5 mins", ["rolled oats", "chia seeds", "almond milk", "greek yogurt", "maple syrup", "wild blueberries"], 1),
        ("Shakshuka", "25 mins", ["eggs", "canned san marzano tomatoes", "bell peppers", "onion", "ground cumin", "smoked paprika", "feta cheese"], 3),
        ("Cold Brew Coffee", "12 hours", ["coarsely ground dark roast coffee", "filtered cold water", "ice cubes", "oat milk"], 4),
        ("Palak Paneer", "35 mins", ["blanched spinach leaves", "paneer cubes", "garlic", "ginger", "garam masala", "fresh cream"], 4),
    ]

    for i in range(50):
        name, prep, ing, srv = recipes_pool[i % len(recipes_pool)]
        dt = random_past_date(2, 350)
        chef_mention = "Chef Kapoor" if i % 4 == 0 else None
        text = f"Recipe for {name}: Prep time {prep}. Key ingredients include {', '.join(ing[:4])}. Serves {srv}."
        if chef_mention:
            text = f"{chef_mention}'s signature recipe for {name}. {text}"
        items.append({
            "text": text,
            "created_at": dt.isoformat(),
            "entities": ["Chef Kapoor"] if chef_mention else [],
            "kind": "recipe",
            "kind_fields": {
                "title": name,
                "prep_time": prep,
                "ingredients": ing,
                "servings": srv,
            },
            "is_sensitive": False,
        })

    # -------------------------------------------------------------
    # 4. SMART CARDS: BOOKS (40 items)
    # -------------------------------------------------------------
    books_pool = [
        ("Designing Data-Intensive Applications", "Martin Kleppmann", "System Design", "Finished"),
        ("Atomic Habits", "James Clear", "Self-Help", "Finished"),
        ("Thinking, Fast and Slow", "Daniel Kahneman", "Psychology", "Finished"),
        ("Deep Work", "Cal Newport", "Productivity", "Finished"),
        ("The Pragmatic Programmer", "Andrew Hunt & David Thomas", "Software Engineering", "Finished"),
        ("Clean Code", "Robert C. Martin", "Software Engineering", "Finished"),
        ("Dune", "Frank Herbert", "Sci-Fi", "Finished"),
        ("Project Hail Mary", "Andy Weir", "Sci-Fi", "Finished"),
        ("Klara and the Sun", "Kazuo Ishiguro", "Literary Fiction", "Reading"),
        ("Chip War", "Chris Miller", "Economics / Tech", "Reading"),
        ("Algorithms to Live By", "Brian Christian & Tom Griffiths", "Computer Science", "To Read"),
        ("The Psychology of Money", "Morgan Housel", "Finance", "Finished"),
        ("Understanding Distributed Systems", "Roberto Vitillo", "System Design", "Finished"),
        ("Zero to One", "Peter Thiel", "Business / Startups", "Finished"),
        ("Good to Great", "Jim Collins", "Business", "To Read"),
        ("Life 3.0", "Max Tegmark", "Artificial Intelligence", "Reading"),
    ]

    for i in range(40):
        title, author, genre, status = books_pool[i % len(books_pool)]
        dt = random_past_date(2, 350)
        recommender = random.choice(["Rohan", "Priya Sharma", "Professor Iyer", "Vikram Malhotra"]) if i % 2 == 0 else None
        text = f"Book: '{title}' by {author} ({genre}). Current status: {status}."
        if recommender:
            text = f"{recommender} recommended reading '{title}' by {author}. Status: {status}."
        items.append({
            "text": text,
            "created_at": dt.isoformat(),
            "entities": [recommender] if recommender else [],
            "kind": "book",
            "kind_fields": {
                "title": title,
                "author": author,
                "genre": genre,
                "status": status,
            },
            "is_sensitive": False,
        })

    # -------------------------------------------------------------
    # 5. SMART CARDS: CONTACTS (35 items)
    # -------------------------------------------------------------
    contacts_pool = [
        ("Dr. Mehta", "Consultant Cardiologist", "Indiranagar Heart Clinic", "+91 98450 12345", "dr.mehta@clinic.in"),
        ("CA Amit Verma", "Chartered Accountant", "Verma & Associates Gurgaon", "+91 98100 54321", "amit@verma-ca.com"),
        ("Priya Sharma", "Staff AI Research Scientist", "DeepMind Bangalore", "+91 99001 88224", "priya.sharma@deepmind.com"),
        ("Chef Kapoor", "Culinary Director", "The Spice Lab Bangalore", "+91 98220 99110", "chef.kapoor@spicelab.com"),
        ("Professor Iyer", "Emeritus Professor of CS", "IIT Delhi", "+91 98111 22334", "iyer@cs.iitd.ac.in"),
        ("Rohan", "Co-Founder & VP Engineering", "VectorFlow Technologies", "+91 97400 33112", "rohan@vectorflow.io"),
        ("Vikram Malhotra", "Partner", "Nexus Venture Partners", "+91 98200 44556", "vikram@nexusvp.com"),
        ("Ananya", "Senior Product Designer", "Flipkart Bangalore", "+91 98451 77889", "ananya@flipkart.com"),
        ("Lawyer Rajesh Narang", "Senior High Court Advocate", "Narang Chambers Delhi", "+91 98110 33445", "rajesh@naranglegal.in"),
        ("Electrician Ramesh", "Licensed Master Electrician", "Indiranagar Home Services", "+91 94480 66778", "ramesh.service@gmail.com"),
        ("Plumber Santosh", "Plumbing Specialist", "Bangalore FastFix", "+91 94490 11223", "santosh.fix@gmail.com"),
        ("Yoga Instructor Meera", "Certified Ashtanga Teacher", "Prana Shala Indiranagar", "+91 98860 55443", "meera.yoga@prana.in"),
    ]

    for i in range(35):
        name, role, org, phone, email = contacts_pool[i % len(contacts_pool)]
        dt = random_past_date(5, 350)
        text = f"Contact details for {name}: {role} at {org}. Phone: {phone}, Email: {email}."
        items.append({
            "text": text,
            "created_at": dt.isoformat(),
            "entities": [name] if name in ENTITIES_DICT else [],
            "kind": "contact",
            "kind_fields": {
                "name": name,
                "role": role,
                "company": org,
                "phone": phone,
                "email": email,
            },
            "is_sensitive": False,
        })

    # -------------------------------------------------------------
    # 6. SMART CARDS: EVENTS (25 items)
    # -------------------------------------------------------------
    events_pool = [
        ("Bangalore Tech Summit 2026", "2026-11-20 09:30 AM", "Bangalore Palace Ground"),
        ("Ananya Wedding Reception", "2026-12-05 07:00 PM", "Taj West End Bangalore"),
        ("AI Research Symposium", "2026-11-25 10:00 AM", "DeepMind Office Auditorium"),
        ("Quarterly Board Meeting", "2026-11-18 02:00 PM", "VectorFlow Office Gurgaon"),
        ("Dr. Mehta Cardiac Stress Test", "2026-11-22 08:30 AM", "Indiranagar Diagnostic Centre"),
        ("Dinner with Rohan and Vikram", "2026-11-16 08:00 PM", "Toit Indiranagar"),
        ("Flight to Mumbai (6E 424)", "2026-11-28 06:15 AM", "Kempegowda International Airport"),
        ("Webinar on RAG and Local LLMs", "2026-11-15 05:00 PM", "Zoom Online"),
    ]

    for i in range(25):
        ename, etime, eloc = events_pool[i % len(events_pool)]
        dt = random_past_date(2, 60)
        text = f"Event scheduled: {ename} on {etime} at {eloc}."
        items.append({
            "text": text,
            "created_at": dt.isoformat(),
            "entities": ["Bangalore"] if "Bangalore" in eloc else ["Indiranagar"] if "Indiranagar" in eloc else [],
            "kind": "event",
            "kind_fields": {
                "event_name": ename,
                "date_time": etime,
                "location": eloc,
            },
            "is_sensitive": False,
        })

    # -------------------------------------------------------------
    # 7. FLASHCARDS & MACHINE LEARNING / CS CONCEPTS (150 items)
    # -------------------------------------------------------------
    flashcard_facts = [
        ("In transformer models, self-attention complexity scales with sequence length as ____.", "O(N^2)", "In Transformers, standard full self-attention scales quadratically with sequence length O(N^2)."),
        ("In distributed systems, the CAP theorem states a system can guarantee at most two of: Consistency, Availability, and ____.", "Partition tolerance", "The CAP theorem states a distributed store can only provide two of Consistency, Availability, and Partition tolerance."),
        ("In vector databases, HNSW stands for Hierarchical Navigable ____ World.", "Small", "HNSW (Hierarchical Navigable Small World) is a state-of-the-art graph-based indexing algorithm for approximate nearest neighbor search."),
        ("In Python, the mechanism that prevents multiple native threads from executing bytecode at once is the ____.", "GIL (Global Interpreter Lock)", "The Global Interpreter Lock (GIL) synchronizes the execution of threads in CPython."),
        ("In SQL indexing, a ____ tree is balanced and keeps data sorted to allow searches, sequential access, insertions, and deletions in logarithmic time.", "B-tree", "B-trees and B+trees are the standard balanced tree indexes used in relational databases."),
        ("In modern LLM inference, the technique of caching keys and values to avoid recomputing past tokens is called ____ cache.", "KV", "KV cache stores previous key and value tensor projections during autoregressive token generation."),
        ("In deep learning optimization, Adam combines the benefits of AdaGrad and ____.", "RMSProp", "Adam computes adaptive learning rates from estimates of first and second moments of gradients (combining AdaGrad and RMSProp)."),
        ("In consensus algorithms, the leader election in Raft uses randomized election ____ to prevent split votes.", "timeouts", "Raft uses randomized election timeouts (e.g. 150-300ms) to ensure split votes are resolved quickly."),
        ("In HTTP caching, the header used to validate if a cached representation is still fresh using a version tag is ____.", "ETag", "The ETag header provides an entity tag token used in If-None-Match conditional requests."),
        ("In Rust memory safety, references cannot outlive their ____.", "lifetimes", "The Rust borrow checker enforces lifetimes to ensure references never point to deallocated memory."),
        ("The metric commonly used in semantic search to compare the angle between two normalized vector embeddings is ____ similarity.", "cosine", "Cosine similarity measures the cosine of the angle between two multi-dimensional vectors."),
        ("In database storage engines, an LSM tree stands for Log-Structured ____-tree.", "Merge", "Log-Structured Merge-trees buffer writes in memory (memtable) before flushing to append-only SSTables on disk."),
    ]

    for i in range(150):
        prompt_q, prompt_a, full_text = flashcard_facts[i % len(flashcard_facts)]
        dt = random_past_date(1, 300)
        items.append({
            "text": full_text,
            "created_at": dt.isoformat(),
            "entities": [],
            "kind": None,
            "kind_fields": None,
            "is_sensitive": False,
            "flashcard": {
                "question": prompt_q,
                "answer": prompt_a,
                "box": i % 5,  # Distributed across Boxes 0, 1, 2, 3, 4!
                "due_days": (i % 5) * 2 - 1,  # Some due today (-1 or 0), some upcoming!
            }
        })

    # -------------------------------------------------------------
    # 8. PRIVACY SHIELD & SENSITIVE NOTES (40 items)
    # -------------------------------------------------------------
    sensitive_samples = [
        ("Medical blood test report from AIIMS: Fasting plasma glucose 98 mg/dL, HbA1c 5.4%, Total Cholesterol 182 mg/dL, Triglycerides 120 mg/dL. Dr. Mehta reviewed and confirmed normal.", ["AIIMS", "Dr. Mehta"]),
        ("HDFC Bank netbanking customer ID 88921045, registered mobile +91 98450 12345, debit card ending in 4091. Keep strictly confidential.", ["HDFC Bank"]),
        ("ICICI Bank salary account: Account Number 004701552319, IFSC code ICIC0000047, PAN Number ABCDW1234K.", ["ICICI Bank"]),
        ("Home WiFi password and Synology NAS admin root credentials: router admin at 192.168.1.1, encryption key WPA3-Personal.", []),
        ("Personal journal reflection on career goals for 2027: considering whether to transition full-time to deep tech startup with Rohan.", ["Rohan"]),
        ("Income tax filing summary for FY 2025-26 from CA Amit Verma: Total taxable income Rs. 28,40,000, Section 80C deductions Rs. 1,50,000, final tax refund expected Rs. 42,000.", ["CA Amit Verma"]),
    ]

    for i in range(40):
        text, ents = sensitive_samples[i % len(sensitive_samples)]
        dt = random_past_date(2, 300)
        items.append({
            "text": text,
            "created_at": dt.isoformat(),
            "entities": ents,
            "kind": None,
            "kind_fields": None,
            "is_sensitive": True,  # MARKED SENSITIVE!
        })

    # -------------------------------------------------------------
    # 9. ACTIONABLE TASKS & ATTENTION ITEMS (160 items)
    # -------------------------------------------------------------
    task_phrases = [
        ("Call CA Amit Verma tomorrow at 3:00 pm about TDS deduction certificate", "High", "tomorrow at 3:00 pm"),
        ("Submit quarterly GST return before Friday 6:00 pm", "High", "before Friday 6:00 pm"),
        ("Dentist appointment with Dr. Mehta today at 4:30 pm in Indiranagar", "High", "today at 4:30 pm"),
        ("Send revised engineering proposal to Priya Sharma at DeepMind by Thursday morning", "Medium", "Thursday morning"),
        ("Order fresh sourdough bread from Bakehouse before 2:00 pm today", "Low", "today at 2:00 pm"),
        ("Renew car insurance policy on HDFC Ergo before November 25th", "High", "November 25th"),
        ("Pay electricity bill to BESCOM before the 15th", "Medium", "the 15th"),
        ("Pick up dry cleaning on the way home from Indiranagar this evening", "Low", "this evening"),
        ("Schedule follow-up meeting with Rohan and Vikram Malhotra for Monday 11:00 am", "Medium", "Monday 11:00 am"),
        ("Review pull request for local semantic search indexing before tonight 9:00 pm", "High", "tonight 9:00 pm"),
        ("Buy flight tickets to Jaipur for the family holiday", "Medium", "next week"),
        ("Water the balcony plants and prune the herbs this weekend", "Low", "this weekend"),
    ]

    for i in range(160):
        t_text, t_pri, t_timing = task_phrases[i % len(task_phrases)]
        dt = random_past_date(1, 40)
        items.append({
            "text": t_text,
            "created_at": dt.isoformat(),
            "entities": ["Dr. Mehta"] if "Dr. Mehta" in t_text else ["Rohan"] if "Rohan" in t_text else ["CA Amit Verma"] if "CA Amit Verma" in t_text else [],
            "kind": None,
            "kind_fields": None,
            "is_sensitive": False,
            "task": {
                "text": t_text,
                "priority": t_pri,
                "timing_text": t_timing,
            }
        })

    # -------------------------------------------------------------
    # 10. GENERAL PROFESSIONAL & PERSONAL SECOND-BRAIN NOTES (Fill up to 1,000)
    # -------------------------------------------------------------
    general_topics = [
        "Met Rohan at Blue Tokai in Gurgaon to discuss distributed graph databases and vector caching layers.",
        "Priya Sharma mentioned a paper on test-time compute scaling: spending more compute at inference yields better reasoning than pure pretraining parameter scale.",
        "Dr. Mehta suggested taking 1000 IU of Vitamin D3 daily during the winter months, especially with mostly indoor desk work.",
        "Ideas for home automation: install Zigbee temperature and humidity sensors in the bedroom and living room.",
        "Summary of discussion with CA Amit Verma regarding Section 54F capital gains exemption on property sale in Bangalore.",
        "Chef Kapoor shared secret to fluffy scrambled eggs: low heat, constant gentle stirring with a silicone spatula, and cold butter cubes right at the end.",
        "Ananya recommended looking into Figma component tokens for redesigning the Life Inbox dark indigo UI theme.",
        "Notes from Professor Iyer's talk at IIT Delhi: distributed consensus is all about agreeing on state transitions in an unreliable network.",
        "Travel notes for upcoming weekend trip to Jaipur: visit Amber Fort early morning, book dinner at 1135 AD, stay near C-Scheme.",
        "Coffee cupping notes at Blue Tokai Indiranagar: washed Ethiopian beans had distinct jasmine floral aroma with bergamot acidity.",
        "Comparison of SQLite vs PostgreSQL for local desktop copilots: SQLite has zero-config and microsecond embedded queries, while Postgres excels in multi-writer concurrency.",
        "Visited Swiggy tech office in Bangalore: discussed their delivery routing algorithm optimizing courier batching with Dijkstra variations.",
        "Zomato food order note: best butter chicken in Gurgaon is from Gulati's, order at least an hour in advance during weekends.",
        "Flipkart Big Billion Days wishlist: ergonomic mesh office chair, 4K 27-inch USB-C monitor, noise-canceling headphones.",
        "Apollo Pharmacy home delivery service: uploaded prescription for regular blood pressure medication, arrives within 2 hours.",
        "Ideas for personal newsletter: write an article about 'How to build an honest AI assistant that admits when it doesn't know the answer'.",
        "Interesting observation on fastembed: running local ONNX models on CPU is fast enough (~15ms per passage) to eliminate cloud embedding API latency entirely.",
        "Routine car service at Honda showroom in Gurgaon: synthetic engine oil replaced, brake pads checked, tire rotation completed.",
        "Weekend cycling route in Bangalore: 35 km loop from Indiranagar through Cubbon Park, Vidhana Soudha, and back via MG Road.",
        "Workout routine: 3 days strength training (squats, bench, deadlifts) + 2 days zone 2 cardio for cardiovascular longevity.",
    ]

    needed = 1000 - len(items)
    for i in range(needed):
        base_text = general_topics[i % len(general_topics)]
        variant_suffix = f" (Ref #{i + 1})"
        dt = random_past_date(1, 355)
        
        # Pick matching entities
        ents = []
        for name in ENTITIES_DICT:
            if name in base_text:
                ents.append(name)

        items.append({
            "text": base_text + (variant_suffix if i >= len(general_topics) else ""),
            "created_at": dt.isoformat(),
            "entities": ents,
            "kind": None,
            "kind_fields": None,
            "is_sensitive": False,
        })

    return items[:1000]


def seed_database():
    owner_id = get_or_create_default_owner_id()
    print(f"[*] Starting high-fidelity seed of 1,000 power notes for owner {owner_id}...")

    notes = generate_notes_data()
    print(f"[*] Generated {len(notes)} rich notes in memory.")

    with SessionLocal() as session:
        # Pre-seed or fetch Entity records
        entity_cache = {}
        for name, etype in (PEOPLE + PLACES + ORGANIZATIONS):
            existing = session.scalars(
                select(EntityRecord).where(
                    EntityRecord.owner_user_id == owner_id,
                    EntityRecord.name_key == name.lower(),
                )
            ).first()
            if not existing:
                existing = EntityRecord(
                    owner_user_id=owner_id,
                    name=name,
                    name_key=name.lower(),
                    entity_type=etype,
                    created_at=NOW.isoformat(),
                )
                session.add(existing)
                session.flush()
            entity_cache[name.lower()] = existing.id

        created_items = 0
        created_cards = 0
        created_flashcards = 0
        created_tasks = 0
        created_entity_links = 0

        for note_data in notes:
            kf = note_data.get("kind_fields")
            kf_str = json.dumps(kf) if kf else None
            item = InboxItemRecord(
                item_type="text",
                raw_text=note_data["text"],
                processing_status="ready",
                owner_user_id=owner_id,
                created_at=note_data["created_at"],
                is_sensitive=note_data.get("is_sensitive", False),
                kind=note_data.get("kind"),
                kind_fields=kf_str,
            )
            session.add(item)
            session.flush()
            created_items += 1

            if note_data.get("kind"):
                created_cards += 1

            # Link Entities
            for ent_name in note_data.get("entities", []):
                ent_id = entity_cache.get(ent_name.lower())
                if ent_id:
                    session.add(ItemEntityRecord(item_id=item.id, entity_id=ent_id))
                    created_entity_links += 1

            # Create Flashcard
            if "flashcard" in note_data:
                fc = note_data["flashcard"]
                due_dt = (TODAY + timedelta(days=fc["due_days"])).isoformat()
                session.add(FlashcardRecord(
                    owner_user_id=owner_id,
                    item_id=item.id,
                    question=fc["question"],
                    answer=fc["answer"],
                    box=fc["box"],
                    due_date=due_dt,
                    created_at=note_data["created_at"],
                ))
                created_flashcards += 1

            # Create Task
            if "task" in note_data:
                t = note_data["task"]
                dedup_key = f"seed_{item.id}_{random.randint(1000, 9999)}"
                task_rec = TaskRecord(
                    owner_user_id=owner_id,
                    text=t["text"],
                    priority=t["priority"],
                    timing_text=t["timing_text"],
                    ai_timing_text=t["timing_text"],
                    timing_confirmed=1,
                    status="open",
                    dedup_key=dedup_key,
                    created_at=note_data["created_at"],
                    updated_at=note_data["created_at"],
                )
                session.add(task_rec)
                session.flush()
                session.add(TaskSourceItemRecord(task_id=task_rec.id, item_id=item.id))
                created_tasks += 1

        session.commit()

    print(f"\n[+] SUCCESS! Seeded:")
    print(f"    - {created_items} Total Inbox Notes")
    print(f"    - {created_cards} Smart Template Cards (Recipes, Books, Receipts, Contacts, Events)")
    print(f"    - {created_entity_links} Entity Connections across People, Places & Orgs")
    print(f"    - {created_flashcards} Leitner Box Flashcards")
    print(f"    - {created_tasks} Actionable Attention Tasks")


if __name__ == "__main__":
    seed_database()
