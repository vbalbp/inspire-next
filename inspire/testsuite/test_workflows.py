# -*- coding: utf-8 -*-
#
# This file is part of INSPIRE.
# Copyright (C) 2014, 2015 CERN.
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

"""Tests for workflows."""

from __future__ import print_function, absolute_import

import os
import pkg_resources
import tempfile

from invenio.celery import celery
from invenio.testsuite import make_test_suite, run_test_suite

from .helpers import WorkflowTasksTestCase


class WorkflowTest(WorkflowTasksTestCase):

    """TODO."""

    def setUp(self):
        """Setup tests."""
        from invenio.modules.knowledge.api import add_kb

        self.create_registries()
        self.record_oai_arxiv_plots = pkg_resources.resource_string(
            'inspire.testsuite',
            os.path.join(
                'workflows',
                'fixtures',
                'oai_arxiv_record_with_plots.xml'
            )
        )
        self.some_record = pkg_resources.resource_string(
            'inspire.testsuite',
            os.path.join(
                'workflows',
                'fixtures',
                'some_record.xml'
            )
        )
        celery.conf['CELERY_ALWAYS_EAGER'] = True

        # Add temp KB
        add_kb('harvesting_fixture_kb')

    def tearDown(self):
        """Clean up created objects."""
        from invenio.modules.workflows.models import Workflow
        from invenio.modules.knowledge.api import delete_kb

        self.delete_objects(
            Workflow.get(Workflow.module_name == 'unit_tests').all())
        self.cleanup_registries()
        delete_kb('harvesting_fixture_kb')

    def test_payload_creation(self):
        """TODO."""
        from invenio.modules.workflows.api import start
        from invenio.modules.workflows.engine import WorkflowStatus

        workflow = start('payload_fixture',
                         data=[self.some_record],
                         module_name="unit_tests")

        self.assertEqual(WorkflowStatus.COMPLETED, workflow.status)
        self.assertTrue(len(workflow.completed_objects) == 1)
        modified_object = workflow.completed_objects[0]

        for l in ['files', 'sips', 'type', 'drafts', 'title']:
            self.assertIn(l, modified_object.data)

    def test_payload_sip_creation(self):
        """TODO."""
        from invenio.modules.workflows.api import start
        from inspire.modules.workflows.models import Payload

        workflow = start('payload_fixture',
                         data=[self.some_record],
                         module_name="unit_tests")
        modified_object = workflow.completed_objects[0]

        p = Payload(modified_object)
        sip = p.get_latest_sip()
        self.assertTrue(sip.metadata)
        # self.assertTrue(sip.package)

    def test_payload_model_creation(self):
        """TODO."""
        from invenio.modules.workflows.api import start

        workflow = start('payload_model_fixture',
                         data=[self.some_record],
                         module_name="unit_tests")
        modified_object = workflow.completed_objects[0]

        p = workflow.workflow_definition.model(modified_object)
        sip = p.get_latest_sip()
        self.assertTrue(sip.metadata)
        # self.assertTrue(sip.package)

    def test_payload_file_creation(self):
        """TODO."""
        from invenio.modules.workflows.models import BibWorkflowObject
        from inspire.modules.workflows.models import Payload
        from inspire.utils.helpers import (
            get_file_by_name,
            add_file_by_name,
        )

        obj = BibWorkflowObject.create_object()
        obj.save()
        obj.data = obj.get_data()  # FIXME hack until workflow 2.0

        payload = Payload.create(workflow_object=obj, type="payload_fixture")
        payload.save()

        fd, filename = tempfile.mkstemp()
        os.close(fd)

        newpath = add_file_by_name(payload, filename)
        self.assertTrue(newpath)

        self.assertTrue(get_file_by_name(payload,
                                         os.path.basename(filename)))
        BibWorkflowObject.delete(obj)

    def test_harvesting_workflow(self):
        """TODO."""
        from invenio.modules.workflows.api import start
        workflow = start('harvesting_fixture',
                         data=[self.record_oai_arxiv_plots],
                         module_name='unit_tests')

        # This workflow should have halted
        self.assertTrue(workflow.halted_objects)


class AgnosticTest(WorkflowTasksTestCase):

    """TODO."""

    def setUp(self):
        """Setup tests."""
        from invenio.modules.deposit.models import Deposition, DepositionType
        from invenio.modules.deposit.registry import deposit_types, \
            deposit_default_type
        from invenio.modules.deposit.form import WebDepositForm
        from invenio.modules.deposit.tasks import prefill_draft, \
            prepare_sip

        celery.conf['CELERY_ALWAYS_EAGER'] = True

        def agnostic_task(obj, eng):
            data_model = eng.workflow_definition.model(obj)
            sip = data_model.get_latest_sip()
            print(sip.metadata)

        class DefaultType(DepositionType):
            pass

        class SimpleRecordTestForm(WebDepositForm):
            pass

        class DepositModelTest(DepositionType):

            """A test workflow for the model."""

            model = Deposition

            draft_definitions = {
                'default': SimpleRecordTestForm,
            }

            workflow = [
                prefill_draft(draft_id='default'),
                prepare_sip(),
                agnostic_task,
            ]

        deposit_types.register(DefaultType)
        deposit_types.register(DepositModelTest)
        deposit_default_type.register(DefaultType)

    def teardown(self):
        """Clean up created objects."""
        from invenio.modules.workflows.models import Workflow
        from invenio.modules.knowledge.api import delete_kb

        self.cleanup_registries()

    def test_agnostic_deposit(self):
        """TODO."""
        from invenio.modules.workflows.api import start
        from invenio.modules.deposit.models import Deposition
        from invenio.ext.login.legacy_user import UserInfo

        u = UserInfo(uid=1)
        d = Deposition.create(u, type='DepositModelTest')
        d.save()
        d.run_workflow()

        completed_object = d.engine.completed_objects[0]
        for l in ['files', 'sips', 'type', 'drafts', 'title']:
            self.assertIn(l, completed_object.data)


TEST_SUITE = make_test_suite(AgnosticTest, WorkflowTest)


if __name__ == "__main__":
    run_test_suite(TEST_SUITE)
