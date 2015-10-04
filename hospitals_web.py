import os
import pymongo
from flask import Flask, session, render_template, request, jsonify

app = Flask(__name__)

search_terms_by_region = {
    'CARILION': ['CARILION'],
    'SENTARA': ['SENTARA'],
    'LEWIS GALE': ['LEWIS GALE', 'LEWIS-GALE', 'LEWISGALE']
}

@app.route("/")
def index():
    data = {
        'regions': search_terms_by_region.keys()
    }
    return render_template('hospitals.html', data=data)

@app.route("/<region>")
def region(region):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['hospital-civil-cases']
    # get all first level searches
    searches = db['cases'].find({'name': {'$in': search_terms_by_region[region]}})
    # organize searches into court regions, too much data to load it all at once
    courts = {}
    names_to_search = set()
    for search in searches:
        court_name = search['court'][5:-14]
        if court_name not in courts:
            courts[court_name] = {
                'full_name': search['court'],
                'case_count': 0
            }
        case_numbers = set()
        for case in search['civilCases']:
            if not should_do_second_level_search(case['Plaintiff'], search['name']):
                continue
            # count names in the second level search
            names_to_search.add(reduce_name(case['Plaintiff']))
            # count unqiue cases in the first level search
            case_numbers.add(case['caseNumber'])
        courts[court_name]['case_count'] += len(case_numbers)
    # count second level searches completed
    searches_to_complete = len(names_to_search) * len(courts)
    searches_completed = db['cases'].count({'name': {'$in': list(names_to_search)}})
    percent_searched = "{0:.0f}%".format(float(searches_completed)/float(searches_to_complete) * 100)
    data = {
        'region': region,
        'courts': courts,
        'names_to_search_count': len(names_to_search),
        'percent_searched': percent_searched,
        'searches_completed': searches_completed,
        'searches_to_complete': searches_to_complete
    }
    return render_template('hospitals.html', data=data)

@app.route("/<region>/first_level/<path:court>")
def court(region, court):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['hospital-civil-cases']
    # get the first level searches
    print 'Fetch first level searches'
    searches = db['cases'].find({'court': court, 'name': {'$in': search_terms_by_region[region]}})
    # get cases and cases number from the searches
    print 'First level cases'
    cases = []
    case_numbers = []
    for search in searches:
        for case in search['civilCases']:
            # remove duplicate cases
            if case['caseNumber'] in case_numbers: continue
            # mark cases that should be ignored becuase they don't fit the model of case we are looking for
            plaintiff_name = reduce_name(case['Plaintiff'])
            if plaintiff_name is None or case['Plaintiff'].startswith(search['name']):
                case['ignore'] = True
            else:
                case['ignore'] = False
                case_numbers.append(case['caseNumber'])
            cases.append(case)
    # get filing type of top level cases
    print 'First level filing types'
    detailed_cases = list(db['detailed_cases'].find({
        'court': court,
        'caseNumber': {'$in': case_numbers}},
        ['FilingType', 'Filed', 'caseNumber']))
    # fill in filing type of top level cases
    for detailed_case in detailed_cases:
        for case in cases:
            if case['caseNumber'] == detailed_case['caseNumber']:
                case['FilingType'] = detailed_case['FilingType']
                case['Filed'] = detailed_case['Filed']
                break
    # load second level cases
    print 'Second level cases'
    second_level_case_names = set()
    for case in cases:
        case_plaintiff_name = reduce_name(case['Plaintiff'])
        if case_plaintiff_name is None: continue
        case['plaintiff_name'] = case_plaintiff_name.replace(',', '').replace('.','').replace(' ', '-')
        second_level_case_names.add(case_plaintiff_name)
    data = {
        'region': region,
        'court': court,
        'court_name': court[5:],
        'civil_cases': cases
    }
    html = render_template('hospital_cases.html', data=data)
    data = {
        'html': html,
        'second_level_case_names': list(second_level_case_names)
    }
    return jsonify(**data)

@app.route("/<region>/second_level/<name>/<path:court>")
def second_level_search(region, name, court):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['hospital-civil-cases']
    cases = []
    case_numbers = set()
    searches = db['cases'].find({'name': name})
    for search in searches:
        for case in search['civilCases']:
            if not case['Plaintiff'].startswith(name):
                case['ignore'] = True
            else:
                case_numbers.add(case['caseNumber'])
            if search['court'] == court:
                case['same_court'] = True
            cases.append({
                'court': search['court'],
                'court_name_short': search['court'][5:-14],
                'details': case
            })
    detailed_cases = list(db['detailed_cases'].find(
        {'caseNumber': {'$in': list(case_numbers)}},
        ['FilingType', 'Filed', 'caseNumber', 'court']))
    for case in cases:
        for detailed_case in detailed_cases:
            if case['court'] == detailed_case['court'] and case['details']['caseNumber'] == detailed_case['caseNumber']:
                case['details']['FilingType'] = detailed_case['FilingType']
                case['details']['Filed'] = detailed_case['Filed']
                break
    return render_template('hospital_sub_cases.html', cases=cases)

@app.route("/<region>/searches_completed/<path:court>")
def court_searches_completed(region, court):
    db = pymongo.MongoClient(os.environ['MONGO_URI'])['hospital-civil-cases']
    plaintiff_names_groups = db['second_level_plaintiff_names'].find({'name': {'$in': search_terms_by_region[region]}})
    plaintiff_names = []
    for group in plaintiff_names_groups:
        plaintiff_names.extend(group['plaintiff_names'])
    searches_to_complete = len(plaintiff_names)
    searches_completed = db['cases'].count({'court': court, 'name': {'$in': list(plaintiff_names)}})
    percent_searched = "{0:.0f}%".format(float(searches_completed)/float(searches_to_complete) * 100)
    data = {
        'region': region,
        'court': court,
        'percent_searched': percent_searched,
        'searches_completed': searches_completed,
        'searches_to_complete': searches_to_complete
    }
    return jsonify(**data)

def should_do_second_level_search(plaintiff_name, first_level_search_name):
    if plaintiff_name.startswith(first_level_search_name): return False
    plaintiff_name = reduce_name(plaintiff_name)
    if plaintiff_name is None: return False
    return True

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
