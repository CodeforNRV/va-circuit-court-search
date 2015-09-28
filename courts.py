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
import flask
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
        courtSearch['cached'] = True
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
        #for civil_case in courtSearch['civilCases']:
        #    if civil_case['Plaintiff'].startswith(name.upper()):
        #        continue
        #    if db['civil_cases'].find_one({'court': court, 'caseNumber': civil_case['caseNumber']}) is not None:
        #        continue
        #    civil_case['court'] = court
        #    civil_case['search_term'] = name.upper()
        #    print civil_case['caseNumber']
        #    try:
        #        get_case_details(opener, civil_case)
        #        sleep(1)
        #    except:
        #        print 'ERROR GETTING CASE DETAILS!', sys.exc_info()
        #    db['civil_cases'].insert(civil_case)
        db['cases'].insert({
            'name': name,
            'court': court,
            'civilCases': courtSearch['civilCases'],
            'dateSaved': datetime.utcnow()
        })
    courtSearch['html'] = render_template('court.html', court=courtSearch)
    return flask.jsonify(**courtSearch)


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
    print cases
    if cases.count() == 0:
        db['searches'].insert({'name': name.upper(), 'searchtime': datetime.utcnow()})
    #for case in cases:
    #    for court in data['courts']:
    #        if case['court'] == court['fullName']:
    #            court['civilCases'] = case['civilCases']
    #            for x in court['civilCases']:
    #                x.pop('_id', None)
    return render_template('search.html', data=data)

@app.route("/")
def index():
    db = pymongo.Connection(os.environ['MONGO_URI'])['court-search-temp']
    prev_searches = list(db['searches'].find())
    for search in prev_searches:
        search['courts_searched'] = db['cases'].find({'name': search['name']}).count()
    return render_template('index.html', searches=prev_searches)

if __name__ == "__main__":
    # Bind to PORT if defined, otherwise default to 5000.
    port = int(os.environ.get('PORT', 5000))
    app.secret_key = 'doesnt-need-to-be-secret'
    app.run(host='0.0.0.0', port=port, debug=True)
