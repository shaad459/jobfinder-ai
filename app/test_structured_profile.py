import json
from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile

text = extract_resume_text("sample_data/Shaad Khan Product Owner.pdf")
profile = extract_structured_profile(text)
print(json.dumps(profile, indent=2))