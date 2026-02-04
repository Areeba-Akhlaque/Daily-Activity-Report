# Pvragon Team Name Mappings
# ==========================
# This file contains the canonical name mappings used across ALL platforms.
# Import this in all fetch scripts to ensure consistency.

# Full name mappings (email/handle -> Display Name)
NAME_MAP = {
    # === Pvragon Team (Primary Emails) ===
    'adriane@pvragon.com': 'Adriane Barredo',
    'alexander@pvragon.com': 'Alexander Pavelko',
    'areeba@pvragon.com': 'Areeba Akhlaque',
    'bilal@pvragon.com': 'Bilal Munir',
    'bradd@pvragon.com': 'Bradd Konert',
    'cherry@pvragon.com': 'Cherry Aznar',
    'cristina@pvragon.com': 'Cristina Villarreal',
    'farhan@pvragon.com': 'Muhammad Farhan',
    'jaime@pvragon.com': 'James Hereford',
    'jerry@pvragon.com': 'Jerry Miller',
    'juan@pvragon.com': 'Juan Vidal',
    'kristi@pvragon.com': 'Kristi Bergeron',
    'mariana@pvragon.com': 'Mariana Gracia Salgado',
    'maz@pvragon.com': 'Maz Tayebi',
    'megha@pvragon.com': 'Megha Sharma',
    'saifullah@pvragon.com': 'Saifullah Khan',
    'sunnat@pvragon.com': 'Sunnat Choriev',
    'roman@pvragon.com': 'Roman Naidenko',
    'saymond@pvragon.com': 'Saymond Montoya',
    'victor@pvragon.com': 'Victor Cheung',
    'tara@pvragon.com': 'Tara Yowell',
    'ricardo@pvragon.com': 'Ricardo',
    
    # === Personal Gmail Accounts ===
    'aleksandar.m.tanaskovic@gmail.com': 'Alexander Pavelko',
    'oO.Pavelko@gmail.com': 'Alexander Pavelko',
    'jkhereford@gmail.com': 'James Hereford',
    'bilalmunir985@gmail.com': 'Bilal Munir',
    'farhan.muhammed9998@gmail.com': 'Muhammad Farhan',
    'areeba.akhlaque@gmail.com': 'Areeba Akhlaque',
    'saifullahkhan.dev@gmail.com': 'Saifullah Khan',
    'saif72437': 'Saifullah Khan',
    'KristiBergeron17@gmail.com': 'Kristi Bergeron',
    'roman.naidenko@gmail.com': 'Roman',
    'xingrandu@gmail.com': 'Xingran Du',
    'kinneybraxton@gmail.com': 'Kinney',
    
    # === Backendless Emails ===
    'alex.pavelko@backendlessmail.com': 'Alexander Pavelko',
    'bogdan@backendless.consulting': 'Bogdan',
    'mark@backendless.com': 'Backendless Support',
    
    # === External Consultants ===
    'michel@davidrobertsconsulting.com': 'Michel',
    'bradd@alphadesign.us': 'Bradd Konert',
    'davidz5@uw.edu': 'David',
    
    # === GitHub Usernames ===
    'Bilal-Munir-Mughal': 'Bilal Munir',
    'mfarhan0304': 'Muhammad Farhan',
    'Areeba-Akhlaque': 'Areeba Akhlaque',
    'Cherry-Aznar': 'Cherry Aznar',
    'codingbreeze': 'Megha Sharma',
    'jkhereford': 'James Hereford',
    'juan-vidal-pvragon': 'Juan Vidal',
    'KBergeron17': 'Kristi Bergeron',
    'SaifullahCICT': 'Saifullah Khan',
    'SunnatChoriyev': 'Sunnat Choriev',
    'sunnatcorp': 'Sunnat Choriev',
    'alex-pvragon': 'Alexander Pavelko',
    'adriane-pvragon': 'Adriane Barredo',
    
    # === ClickUp Usernames ===
    'alexanderpavelko': 'Alexander Pavelko',
    'reebaakhlaque': 'Areeba Akhlaque',
    'bilalmughal': 'Bilal Munir',
    'cherryaznar': 'Cherry Aznar',
    'james hereford': 'James Hereford',
    'jameshereford': 'James Hereford',
    'juanvidal': 'Juan Vidal',
    'kristibergeron': 'Kristi Bergeron',
    'muhammadfarhan': 'Muhammad Farhan',
    'saifullahkhan': 'Saifullah Khan',
    'sunnatchoriyev': 'Sunnat Choriev',
    
    # === Figma Handles ===
    'Alexander Pavelko': 'Alexander Pavelko',
    'Areeba Akhlaque': 'Areeba Akhlaque',
    'Bilal Munir': 'Bilal Munir',
    'Cherry Aznar': 'Cherry Aznar',
    'James Hereford': 'James Hereford',
    'Juan Vidal': 'Juan Vidal',
    'Kristi Bergeron': 'Kristi Bergeron',
    'Muhammad Farhan': 'Muhammad Farhan',
    'Saifullah Khan': 'Saifullah Khan',
    'Sunnat Choriev': 'Sunnat Choriev',
    
    # === Google Workspace Display Names ===
    # These are how names appear in Google Workspace reports
    'A.S. Johan': 'A.S. Johan',
    'Adriane Barredo': 'Adriane Barredo',
    'Alexander': 'Alexander Pavelko',
    'Bradd Schofield': 'Bradd Schofield',
    'Jeniffer Rosa': 'Jeniffer Rosa',
    'Keeko Villaveces': 'Keeko Villaveces',
    'Lena Klapper': 'Lena Klapper',
    'Mariana Gracia Salgado': 'Mariana Gracia Salgado',
    'Roman Naidenko': 'Roman Naidenko',
    'Saymond Montoya': 'Saymond Montoya',
    'Tara Yowell': 'Tara Yowell',
    'Victor Cheung': 'Victor Cheung',
}

# Exclusion patterns - accounts to skip
EXCLUDE_PATTERNS = [
    # Kelly accounts
    'kelly@pvragon.com', 'Kelly', 'Kelly Hereford',
    
    # System accounts
    'build@pvragon.com', 'careers@pvragon.com', 'employees@pvragon.com',
    'support@pvragon.com', 'gcp-organization-admins@pvragon.com',
    'rc-eng-notifications@', 'service-admins@', 'softstackers@',
    'Eng', 'Errors-Rc', 'RefCheqr-eng-notifications@pvragon.com', '/hd/domain/pvragon.com',
    'support-getmilotrack@pvragon.com', 'contact-getmilotrack@pvragon.com', 'contact-refio.so@pvragon.com',
    
    # Bots
    'dependabot[bot]', 'vercel[bot]', 'github-actions[bot]',
    
    # External/one-off
    'melanie@novastarcreative.com', 's.bruton@okridecare.com',
    'sarahjbru@gmail.com', 'lucah.e.hereford@gmail.com',
    'ericaeggers@google.com', 'c.hedrick@davidrobertsconsulting.com',
    'Casey Hedrick', 'Toni McGee', 'marcpetrelis1997@gmail.com', 
    'alex.gramajo@softstackers.com', 'lucas.acosta@softstackers.com', 
    'ml@zipdev.com',
]


def map_name(identifier):
    """
    Map an email/username/handle to a friendly display name.
    Case-insensitive lookup.
    """
    if not identifier or identifier == 'Unknown':
        return 'Unknown'
    
    identifier_lower = identifier.lower().strip()
    
    # Direct lookup
    for key, value in NAME_MAP.items():
        if key.lower() == identifier_lower:
            return value
    
    # Keyword-based fallback (for partial matches)
    keyword_map = {
        'jkhereford': 'James Hereford',
        'jaime': 'James Hereford',
        'james': 'James Hereford',
        'alexander': 'Alexander Pavelko',
        'pavelko': 'Alexander Pavelko',
        'areeba': 'Areeba Akhlaque',
        'bilal': 'Bilal Munir',
        'mughal': 'Bilal Munir',
        'farhan': 'Muhammad Farhan',
        'cherry': 'Cherry Aznar',
        'kristi': 'Kristi Bergeron',
        'bergeron': 'Kristi Bergeron',
        'saifullah': 'Saifullah Khan',
        'sunnat': 'Sunnat Choriev',
        'javidal10': 'Juan Vidal',
        'juan': 'Juan Vidal',
        'roman naidenko': 'Roman Naidenko',
        'roman': 'Roman Naidenko',
        'victor cheung': 'Victor Cheung',
        'victor': 'Victor Cheung',
        'saymond montoya': 'Saymond Montoya',
        'saymond': 'Saymond Montoya',
        'tara yowell': 'Tara Yowell',
        'tara': 'Tara Yowell',
        'ricardo': 'Ricardo',
        'bogdan': 'Bogdan',
        'xingran': 'Xingran Du',
        'kinney': 'Kinney',
        'michel': 'Michel',
        'david': 'David',
        'bradd schofield': 'Bradd Schofield',
        'bradd konert': 'Bradd Konert',
        'bradd': 'Bradd Schofield',
        'megha': 'Megha Sharma',
        'adriane': 'Adriane Barredo',
        'mariana': 'Mariana Gracia Salgado',
        'cristina': 'Cristina Villarreal',
        'maz': 'Maz Tayebi',
        'jerry': 'Jerry Miller',
        'jeniffer': 'Jeniffer Rosa',
        'keeko': 'Keeko Villaveces',
        'lena': 'Lena Klapper',
        'a.s. johan': 'A.S. Johan',
        'johan': 'A.S. Johan',
    }
    
    for keyword, name in keyword_map.items():
        if keyword in identifier_lower:
            return name
    
    # Return original if no mapping found
    return identifier


def should_exclude(name):
    """Check if a name/email should be excluded from reports."""
    if not name:
        return True
    
    name_lower = name.lower().strip()
    
    for pattern in EXCLUDE_PATTERNS:
        if pattern.lower() in name_lower:
            return True
    
    return False
