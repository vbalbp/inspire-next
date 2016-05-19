# -*- coding: utf-8 -*-
#
# This file is part of INSPIRE.
# Copyright (C) 2014, 2015, 2016 CERN.
#
# INSPIRE is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# INSPIRE is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with INSPIRE; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

"""Tasks to check if the incoming record already exist."""

import datetime
import os
import re
import requests
import six
import traceback

from functools import wraps

from flask import current_app

from inspirehep.utils.arxiv import get_clean_arXiv_id
from inspirehep.utils.datefilter import date_older_than
from inspirehep.utils.record import get_value


def search(query):
    """Perform a search and returns the matching ids."""
    params = dict(p=query, of='id')

    try:
        return requests.get(
            current_app.config["WORKFLOWS_MATCH_REMOTE_SERVER_URL"],
            params=params
        ).json()
    except requests.ConnectionError:
        current_app.logger.error(
            "Error connecting to remote server:\n {0}".format(
                traceback.format_exc()
            )
        )
        raise
    except ValueError:
        current_app.logger.error(
            "Error decoding results from remote server:\n {0}".format(
                traceback.format_exc()
            )
        )
        raise


def match_by_arxiv_id(record):
    """Match by arXiv identifier."""
    arxiv_id = get_clean_arXiv_id(record)

    if arxiv_id:
        query = '035:"{0}"'.format(arxiv_id)
        return search(query)

    return list()


def match_by_doi(record):
    """Match by DOIs."""
    dois = get_value(record, 'dois.value', [])

    result = set()
    for doi in dois:
        query = '0247:"{0}"'.format(doi)
        result.update(search(query))

    return list(result)


def match_legacy_inspire(obj, eng):
    """Return True if the record already exists in INSPIRE.

    Searches by arXiv identifier and DOI, updates extra_data with results.
    """
    response = list(
        set(match_by_arxiv_id(obj.data)) | set(match_by_doi(obj.data))
    )

    base_url = current_app.config["SERVER_NAME"]
    if not re.match('^https?://', base_url):
        base_url = 'http://{}'.format(base_url)
    obj.extra_data['record_matches'] = {
        "recids": [str(recid) for recid in response],
        "records": [],
        "base_url": os.path.join(base_url, 'record')
    }
    return bool(obj.extra_data['record_matches']['recids'])


def match_with_invenio_matcher(queries=None, index="records-hep", doc_type="hep"):
    """Match record using Invenio Matcher."""
    @wraps(match_with_invenio_matcher)
    def _match_with_invenio_matcher(obj, eng):
        from invenio_matcher.api import match as _match

        if queries is None:
            queries_ = [
                {'type': 'exact', 'match': 'dois.value'},
                {'type': 'exact', 'match': 'arxiv_eprints.value'}
            ]
        else:
            queries_ = queries

        record_matches = {
            "recids": [],
            "records": [],
            "base_url": os.path.join(
                current_app.config["SERVER_NAME"],
                'record'
            )
        }

        record = {}
        record['dois.value'] = get_value(obj.data, 'dois.value')
        record['arxiv_eprints.value'] = get_value(
            obj.data, 'arxiv_eprints.value'
        )
        for matched_record in _match(
            record,
            queries=queries_,
            index=index,
            doc_type=doc_type
        ):
            matched_recid = matched_record.record.get('id')
            record_matches['recids'].append(matched_recid)
            record_matches['records'].append({
                "source": matched_record.record.dumps(),
                "score": matched_record.score
            })

        obj.extra_data["record_matches"] = record_matches

        return bool(record_matches['recids'])
    return _match_with_invenio_matcher


def was_already_harvested(record):
    """Return True if the record was already harvested.

    We use the following heuristic: if the record belongs to one of the
    CORE categories then it was probably ingested in some other way.
    """
    categories = get_value(record, 'subject_terms.term', [])
    for category in categories:
        if category.lower() in current_app.config.get('INSPIRE_ACCEPTED_CATEGORIES', []):
            return True


def is_too_old(record, days_ago=5):
    """Return True if the record is more than days_ago days old.

    If the record is older then it's probably an update of an earlier
    record, and we don't want those.
    """
    earliest_date = record.get('earliest_date', '')
    if not earliest_date:
        earliest_date = record.get('preprint_date', '')
    parsed_date = datetime.datetime.strptime(earliest_date, "%Y-%m-%d")
    if date_older_than(parsed_date,
                       datetime.datetime.now(),
                       days=days_ago):
        return True


def record_exists(obj, eng):
    """Check if record exist in the system."""
    # Use matcher if not on production
    if not current_app.config.get('PRODUCTION_MODE'):
        if match_with_invenio_matcher()(obj, eng):
            obj.log.info("Record already exists in INSPIRE (using matcher).")
            return True
    else:
        obj.log.warning("Remote match is deprecated.")
        if match(obj, eng):
            obj.log.info("Record already exists in INSPIRE.")
            return True
    return False


def already_harvested(obj, eng):
    """Check if record is already harvested."""
    if current_app.config.get('PRODUCTION_MODE'):
        if was_already_harvested(obj.data):
            obj.log.info('Record is already being harvested on INSPIRE.')
            return True
    return False


def previously_rejected(days_ago=None):
    """Check if record exist on INSPIRE or already rejected."""
    @wraps(previously_rejected)
    def _previously_rejected(obj, eng):
        if current_app.config.get('PRODUCTION_MODE'):

            if days_ago is None:
                _days_ago = current_app.config.get('INSPIRE_ACCEPTANCE_TIMEOUT', 5)
            else:
                _days_ago = days_ago

            if is_too_old(obj.data, days_ago=_days_ago):
                obj.log.info("Record is likely rejected previously.")
                return True
        return False

    return _previously_rejected


def exists_in_holding_pen(obj, eng):
    """Check if a record exists in HP by looking in given KB."""
    from invenio_search import RecordsSearch
    from invenio_workflows_ui.utils import obj_or_import_string
    from invenio_workflows_ui.search import default_query_factory

    conf = current_app.config['WORKFLOWS_UI_REST_ENDPOINT']
    index = conf.get('search_index')
    doc_type = conf.get('search_type')
    searcher = RecordsSearch(index=index, doc_type=doc_type).params(version=True)
    search_factory = conf.get(
        'search_factory', default_query_factory
    )
    search_factory = obj_or_import_string(search_factory)

    identifiers = []
    for field, lookup in six.iteritems(
            current_app.config.get("HOLDING_PEN_MATCH_MAPPING", {})):
        # Add quotes around to make the search exact
        identifiers += ['{0}:"{1}"'.format(field, i)
                        for i in get_value(obj.data, lookup, [])]
    # Search for any existing record in Holding Pen, exclude self
    if identifiers:
        search, dummy = search_factory(
            None, searcher,
            sort="_workflow.modified",
            q=" OR ".join(identifiers)
        )
        search_result = search.execute()
        id_list = [int(hit.id) for hit in search_result.hits]
        if set(id_list) - set([obj.id]):
            obj.log.info("Record already found in Holding Pen ({0})".format(
                id_list
            ))
            obj.extra_data["holdingpen_ids"] = id_list
            return True
    return False


def delete_self_and_stop_processing(obj, eng):
    """Delete both versions of itself and stops the workflow."""
    from invenio_db import db
    db.session.delete(obj)
    eng.skipToken()


def stop_processing(obj, eng):
    """Stop processing for object and return as completed."""
    eng.stopProcessing()


def update_old_object(obj, eng):
    """Update the data of the old object with the new data."""
    from invenio_workflows import WorkflowObject
    holdingpen_ids = obj.extra_data.get("holdingpen_ids", [])
    if holdingpen_ids and len(holdingpen_ids) == 1:
        old_object = WorkflowObject.query.get(holdingpen_ids[0])
        if old_object.workflow.name == eng.name:
            # Update record if part of the same workflow
            old_object.set_data(obj.data)
            old_object.save()
    else:
        msg = "Cannot update old object, non valid ids: {0}".format(holdingpen_ids)
        obj.log.error(msg)
        raise Exception(msg)