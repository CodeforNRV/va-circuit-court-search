import cookielib
import urllib
import urllib2
import os
import pickle
import pymongo
import pygal
import locale
import re
import sys
from bson.son import SON
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask import Flask, session, render_template, request
from pprint import pprint
from time import sleep

app = Flask(__name__)

user_agent = u"Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.6; en-US; " + \
    u"rv:1.9.2.11) Gecko/20101012 Firefox/3.6.11"

def get_data_from_table(case, table):
    table_cells = table.find_all('td')
    for cell in table_cells:
        strings = list(cell.stripped_strings)
        if len(strings) < 2:
            continue
        name = strings[0].encode('ascii', 'ignore') \
                         .replace(':', '').replace(' ', '')
        value = strings[1].encode('ascii', 'ignore')
        case[name] = value

def get_names_from_table(case, table, party):
    case[party] = [list(x.stripped_strings) for x in table.find_all('li')]

def get_hearings_from_table(case, table):
    case['Hearings'] = []
    rows = table.find_all('tr')
    col_names = [x.encode('ascii') for x in rows.pop(0).stripped_strings]
    col_names[0] = 'Number'
    for row in rows:
        hearing = {}
        for i, col in enumerate(row.find_all('td')):
            val = col.get_text(strip=True) \
                     .encode('ascii', 'ignore')
            if val != '':
                hearing[col_names[i]] = val
        hearing_dt = hearing['Date'] + hearing['Time']
        hearing['Datetime'] = datetime.strptime(hearing_dt, "%m/%d/%y%I:%M%p")
        case['Hearings'].append(hearing)

def get_disposition_from_table(case, table):
    cells = [list(x.stripped_strings) for x in table.find_all('li')]
    for cell in cells:
        if len(cell) < 2:
            continue
        name = cell[0].encode('ascii', 'ignore') \
                      .replace(':', '').replace(' ', '')
        value = cell[1].encode('ascii', 'ignore')
        case[name] = value

def value_to_datetime(case, name):
    case[name] = datetime.strptime(case[name], "%m/%d/%y")

def get_case_details(opener, case):
    data = urllib.urlencode({
        'courtId':case['court'][:3],
        'caseNo':case['caseNumber'],
        'categorySelected':'CIVIL'
    })
    url = u"http://ewsocis1.courts.state.va.us/CJISWeb/CaseDetail.do"
    html = BeautifulSoup(opener.open(url, data).read())
    tables = html.find_all('table')
    details_table = tables[4]
    plaintiffs_table = tables[8]
    defendants_table = tables[10]
    hearings_table = tables[12]
    disposition_table = tables[14]
    get_data_from_table(case, details_table)
    get_names_from_table(case, plaintiffs_table, 'Plaintiffs')
    get_names_from_table(case, defendants_table, 'Defendants')
    get_hearings_from_table(case, hearings_table)
    get_disposition_from_table(case, disposition_table)
    if 'Filed' in case:
        value_to_datetime(case, 'Filed')

def getCases(html, name, names):
    for row in html.find(class_="nameList").find_all('tr'):
        cols = row.find_all('td')
        if len(cols) > 4:
            if name not in cols[1].string:
                return True
            names.append({
                'caseNumber': cols[0].span.a.string.strip(),
                'name': cols[1].string.strip(),
                'charge': cols[2].string.strip(),
                'date': cols[3].string.strip(),
                'status': cols[4].string.strip()
            })
        elif len(cols) > 3:
            if name not in cols[1].get_text() and name not in \
                    cols[2].get_text():
                return True
            first_party = cols[1].get_text() \
                                 .replace('\t', '') \
                                 .replace('\r', '') \
                                 .replace('\n', '') \
                                 .split(':')
            second_party = cols[2].get_text() \
                                  .replace('\t', '') \
                                  .replace('\r', '') \
                                  .replace('\n', '') \
                                  .split(':')
            names.append({
                'caseNumber': cols[0].span.a.string.strip(),
                'name': cols[1].get_text(),
                'otherName': cols[2].get_text(),
                first_party[0]: first_party[1].strip(),
                second_party[0]: second_party[1].strip(),
                'status': cols[3].string.strip()
            })
    return False


def lookupCases(opener, name, court, division):
    cases = []
    data = urllib.urlencode({
        'category': division,
        'lastName': name,
        'courtId': court,
        'submitValue': 'N'})
    cases_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/Search.do"
    searchResults = opener.open(cases_url, data)
    html = searchResults.read()
    done = getCases(BeautifulSoup(html), name, cases)

    data = urllib.urlencode({
        'courtId': court,
        'pagelink': 'Next',
        'lastCaseProcessed': '',
        'firstCaseProcessed': '',
        'lastNameProcessed': '',
        'firstNameProcessed': '',
        'category': division,
        'firstCaseSerialNumber': 0,
        'lastCaseSerialNumber': 0,
        'searchType': '',
        'emptyList': ''})

    search_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/Search.do"
    count = 1
    while(not done):
        sleep(1)
        print 'Search', str(count)
        count += 1
        search_results = opener.open(search_url, data)
        html = search_results.read()
        done = getCases(BeautifulSoup(html), name, cases)
    return cases

@app.route("/search/<name>/court/<path:court>")
def searchCourt(name, court):
    if 'cookies' not in session:
        return "Error. Please reload the page."

    courtId = court[:3]
    courtSearch = {'name': court[5:], 'id': courtId}

    db = pymongo.Connection(os.environ['MONGO_URI'])['court-search-temp']
    cases = db['cases'].find_one({'name': name, 'court': court})
    if cases is not None:
        print 'Found cached search'
        courtSearch['civilCases'] = cases['civilCases']
    else:
        cookieJar = cookielib.CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar))
        opener.addheaders = [('User-Agent', user_agent)]

        for cookie in pickle.loads(session['cookies']):
            cookieJar.set_cookie(cookie)

        data = urllib.urlencode({
            'courtId': courtId,
            'courtType': 'C',
            'caseType': 'ALL',
            'testdos': False,
            'sessionCreate': 'NEW',
            'whichsystem': court})
        place_url = u"http://ewsocis1.courts.state.va.us/CJISWeb/MainMenu.do"
        opener.open(place_url, data)
        courtSearch['civilCases'] = lookupCases(opener, name.upper(),
                                                courtId, 'CIVIL')

    if cases is None:
        print 'Caching search'
        for civil_case in courtSearch['civilCases']:
            if civil_case['Plaintiff'].startswith(name.upper()):
                continue
            if db['civil_cases'].find_one({'court': court, 'caseNumber': civil_case['caseNumber']}) is not None:
                continue
            civil_case['court'] = court
            civil_case['search_term'] = name.upper()
            print civil_case['caseNumber']
            try:
                get_case_details(opener, civil_case)
                sleep(1)
            except:
                print 'ERROR GETTING CASE DETAILS!', sys.exc_info()
            db['civil_cases'].insert(civil_case)
        db['cases'].insert({
            'name': name,
            'court': court,
            'civilCases': courtSearch['civilCases'],
            'dateSaved': datetime.utcnow()
        })
    return render_template('court.html', court=courtSearch)


@app.route("/search/<name>")
def search(name):
    db = pymongo.Connection(os.environ['MONGO_URI'])['court-search-temp']
    cookieJar = cookielib.CookieJar()
    opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar))
    opener.addheaders = [('User-Agent', user_agent)]
    home = opener.open('http://ewsocis1.courts.state.va.us/CJISWeb/circuit.jsp')
    session['cookies'] = pickle.dumps(list(cookieJar))

    courts = []
    html = BeautifulSoup(home.read())
    for option in html.find_all('option'):
        courts.append({
            'fullName': option['value'],
            'id': option['value'][:3],
            'name': option['value'][5:]
        })
    data = {'name': name.upper(), 'courts': courts}
    cases = db['cases'].find({'name': name.upper()})
    for case in cases:
        for court in data['courts']:
            if case['court'] == court['fullName']:
                court['civilCases'] = case['civilCases']
                for x in court['civilCases']:
                    x.pop('_id', None)
    return render_template('search.html', data=data)


@app.route("/")
def index():
    return render_template('index.html')

@app.route("/charges")
def charges():
    client = pymongo.MongoClient(os.environ['MONGO_URI'])
    db = client.va_circuit_court
    charges = db.criminal_cases.aggregate([
        {'$group':{
            '_id': {
                'CodeSection': '$CodeSection',
                'Race': '$Race'
            },
            'charge': {'$first': '$Charge'},
            'court': {'$first': '$Court'},
            'caseNumber': {'$first': '$CaseNumber'},
            'avgSentence': {'$avg': '$SentenceTimeDays'},
            'avgSentenceSuspended': {'$avg': '$SentenceSuspendedDays'},
            'count': {'$sum': 1}
        }},
        {'$group':{
            '_id': {
                'CodeSection': '$_id.CodeSection'
            },
            'races': {'$push': {
                'race': '$_id.Race',
                'avgSentence': '$avgSentence',
                'avgSentenceSuspended': '$avgSentenceSuspended',
                'count': '$count'
            }},
            'count': {'$sum': '$count'},
            'avgSentence': {'$avg': '$avgSentence'},
            'avgSentenceSuspended': {'$avg': '$avgSentenceSuspended'},
            'charge': {'$first': '$charge'},
            'court': {'$first': '$court'},
            'caseNumber': {'$first': '$caseNumber'}
        }},
        {'$match' : {
            'count' : {'$gt' : 50}
        }},
        {'$sort': SON([
            ('_id.CodeSection', 1)
        ])}
    ])['result']

    charges_amended = db.criminal_cases.aggregate([
        {'$match': {'AmendedCharge': {'$ne': None}}},
        {'$group':{
            '_id': {
                'CodeSection': '$CodeSection',
                'Race': '$Race'
            },
            'charge': {'$first': '$Charge'},
            'court': {'$first': '$Court'},
            'caseNumber': {'$first': '$CaseNumber'},
            'avgSentence': {'$avg': '$SentenceTimeDays'},
            'avgSentenceSuspended': {'$avg': '$SentenceSuspendedDays'},
            'count': {'$sum': 1}
        }},
        {'$group':{
            '_id': {
                'CodeSection': '$_id.CodeSection'
            },
            'races': {'$push': {
                'race': '$_id.Race',
                'avgSentence': '$avgSentence',
                'avgSentenceSuspended': '$avgSentenceSuspended',
                'count': '$count'
            }},
            'count': {'$sum': '$count'},
            'avgSentence': {'$avg': '$avgSentence'},
            'avgSentenceSuspended': {'$avg': '$avgSentenceSuspended'},
            'charge': {'$first': '$charge'},
            'court': {'$first': '$court'},
            'caseNumber': {'$first': '$caseNumber'}
        }},
        {'$sort': SON([
            ('_id.CodeSection', 1)
        ])}
    ])['result']

    for charge in charges:
        charge['amended'] = {
            'count': 0,
            'avgSentence': 0,
            'avgSentenceSuspended': 0,
            'races': []
        }
        for charge_amended in charges_amended:
            if charge_amended['_id']['CodeSection'] == charge['_id']['CodeSection']:
                charge['amended'] = charge_amended
                break
        charge['races_dict'] = {
            'White Caucasian (Non-Hispanic)': {
                'count': 0,
                'avgSentence': 0,
                'avgSentenceSuspended': 0
            },
            'Black (Non-Hispanic)': {
                'count': 0,
                'avgSentence': 0,
                'avgSentenceSuspended': 0
            }
        }
        charge['amended']['races_dict'] = {
            'White Caucasian (Non-Hispanic)': {
                'count': 0,
                'avgSentence': 0,
                'avgSentenceSuspended': 0
            },
            'Black (Non-Hispanic)': {
                'count': 0,
                'avgSentence': 0,
                'avgSentenceSuspended': 0
            }
        }
        for race in charge['races']:
            if 'race' in race:
                charge['races_dict'][race['race']] = race
        for race in charge['amended']['races']:
            if 'race' in race:
                charge['amended']['races_dict'][race['race']] = race

    return render_template('charges.html', charges=charges, charges_amended=charges_amended)

@app.route("/opendata")
def open_data():
    client = pymongo.MongoClient(os.environ['MONGO_CASES_URI'])
    db = client.va_circuit_court_cases
    locale.resetlocale()
    case_number_count = locale.format("%d", db.case_numbers.count(), grouping=True)
    data = {
        'case_number_count': case_number_count
    }
    return render_template('open_data.html', data=data)

@app.route("/opendata/progress")
def open_data_progress():
    client = pymongo.MongoClient(os.environ['MONGO_CASES_URI'])
    db = client.va_circuit_court_cases
    data = db.case_numbers.aggregate([
        {'$sort': SON([
            ('court', 1),
            ('name', 1)
        ])},
        {'$group':{
            '_id': {
                'court': '$court'
            },
            'firstName': {'$first': '$name'},
            'lastName': {'$last': '$name'},
            'count': {'$sum': 1}
        }},
        {'$sort': SON([
            ('_id.court', 1)
        ])}
    ])['result']
    chart = pygal.HorizontalBar(style=pygal.style.RedBlueStyle, show_legend=False, height=1500)
    chart.x_labels = [x['_id']['court'] for x in data][::-1]
    chart.add('', [x['count'] for x in data][::-1])
    return chart.render() + str(render_template('open_data_progress.html', data=data))

@app.route("/sampleLetter")
def sample_letter():
    return render_template('sample_letter.html')

@app.route("/stats")
def stats():
    return render_template('stats.html')

@app.route("/stats/graph", methods=['POST'])
def graph():
    categories = request.get_json(force=True)['categories']
    print categories

    category = categories[0]['category']
    sub_category = categories[1]['category']
    sort_by = categories[0]['sort']
    if sort_by == 'alpha':
        sort_by = '_id.' + category
    sort_direction = int(categories[0]['sortDirection'])
    sort = (sort_by, sort_direction)
    filters = categories[0]['filter']

    first_group_stage = {'$group':{
        '_id': {
            category: '$' + category
        },
        'count': {'$sum': 1}
    }}
    second_group_stage = None
    if sub_category != '':
        first_group_stage['$group']['_id'][sub_category] = '$' + sub_category
        second_group_stage =  {'$group':{
            '_id': {
                category: '$_id.' + category,
            },
            'data': {'$push': {
                sub_category: '$_id.' + sub_category,
                'count': '$count'
            }},
            'count': {'$sum': '$count'}
        }}
    sort_stage = {'$sort': SON([
        sort
    ])}

    client = pymongo.MongoClient(os.environ['MONGO_URI'])
    db = client.va_circuit_court
    data = None
    if second_group_stage is None:
        data = db.criminal_cases.aggregate([
            first_group_stage,
            sort_stage
        ])['result']
    else:
        data = db.criminal_cases.aggregate([
            first_group_stage,
            second_group_stage,
            sort_stage
        ])['result']

    sub_category_names = []
    if sub_category != '':
        for group in data:
            for sub_category_group in group['data']:
                sub_category_name = 'None'
                if sub_category in sub_category_group:
                    sub_category_name = sub_category_group[sub_category]
                if sub_category_name not in sub_category_names:
                    sub_category_names.append(sub_category_name)
                group[sub_category_name] = sub_category_group['count']
    print pprint(data)

    pprint(sub_category_names)
    values = [str(x['_id'][category]) for x in data]
    labels = [v for v in values if v not in filters][:20]

    bar_chart = pygal.Bar(height=450, style=LightStyle, x_label_rotation=70)
    bar_chart.title = 'VA Circuit Court Cases in 2014'
    bar_chart.x_labels = labels
    if sub_category == '':
        bar_chart.add(category, [x['count'] for x in data if str(x['_id'][category]) not in filters][:20])
    else:
        for item in sub_category_names[:10]:
            item_counts = []
            for x in data:
                if str(x['_id'][category]) in filters: continue
                if item in x:
                    item_counts.append(x[item])
                else:
                    item_counts.append(0)
            bar_chart.add(item, item_counts[:20])

    return str(render_template('stats_filters.html',
        category=category,
        filter_values=sorted(values),
        filters_unchecked=filters)) + \
        bar_chart.render()

if __name__ == "__main__":
    # Bind to PORT if defined, otherwise default to 5000.
    port = int(os.environ.get('PORT', 5000))
    app.secret_key = 'doesnt-need-to-be-secret'
    app.run(host='0.0.0.0', port=port, debug=True)
