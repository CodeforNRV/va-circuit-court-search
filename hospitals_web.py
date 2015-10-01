import os
import pymongo
from flask import Flask, session, render_template, request

app = Flask(__name__)

search_terms_by_region = {
    'CARILION': ['CARILION'],
    'SENTARA': ['SENTARA']
}

@app.route("/")
def index():
    data = {
        'regions': [
            'CARILION',
            'SENTARA'
        ]
    }
    return render_template('hospitals.html', data=data)

@app.route("/<region>")
def region(region):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['court-search-temp']
    searches = db['cases'].find({'name': {'$in': search_terms_by_region[region]}})
    courts = []
    for search in searches:
        courts.append({
            'full_name': search['court'],
            'name': search['court'][5:].replace('Circuit Court', ''),
            'case_count': len(search['civilCases'])
        })
    data = {
        'region': region,
        'courts': courts
    }
    return render_template('hospitals.html', data=data)

@app.route("/<region>/<path:court>")
def court(region, court):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['court-search-temp']
    searches = db['cases'].find({'court': court, 'name': {'$in': search_terms_by_region[region]}})
    cases = []
    case_numbers = set()
    for search in searches:
        for case in search['civilCases']:
            plaintiff_name = reduce_name(case['Plaintiff'])
            if plaintiff_name is None or case['Plaintiff'].startswith(search['name']):
                case['ignore'] = True
            else:
                case['ignore'] = False
                case_numbers.add(case['caseNumber'])
            cases.append(case)
    detailed_cases = list(db['detailed_cases'].find({
        'court': court,
        'caseNumber': {'$in': list(case_numbers)}},
        ['FilingType', 'caseNumber']))
    for detailed_case in detailed_cases:
        for case in cases:
            if case['caseNumber'] == detailed_case['caseNumber']:
                case['FilingType'] = detailed_case['FilingType']
                break
    data = {
        'region': region,
        'court': court,
        'court_name': court[5:],
        'civil_cases': cases
    }
    return render_template('hospital_cases.html', data=data)

def reduce_name(name):
    if 'COUNTY OF' in name: return None
    if 'CITY OF' in name: return None
    name = name.replace(';',' ')
    name_parts = name.split(' ')
    if len(name_parts) < 2 or not name_parts[0].endswith(','): return None
    name = ' '.join(name_parts[:2])
    return name

if __name__ == "__main__":
    # Bind to PORT if defined, otherwise default to 5000.
    port = int(os.environ.get('PORT', 5000))
    app.secret_key = 'doesnt-need-to-be-secret'
    app.run(host='0.0.0.0', port=port, debug=True)
