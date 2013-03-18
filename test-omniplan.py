#!/usr/bin/env python

import unittest
from omniplan import Task, FourCharacterCode, OmniPlanDocument
import omniplan

class TestFourCharacterCode(unittest.TestCase):

    def test_fourcc(self):
        self.assertEquals(FourCharacterCode.string_to_value('OPTS'), 1330664531)
        self.assertEquals(FourCharacterCode.value_to_string(1330664531), 'OPTS')


class TestWorkDayTimeInterval(unittest.TestCase):
    
    def test_work_day_time_interval(self):
        self.assertEquals(omniplan.WorkDayTimeInterval(workdays=1).seconds(), 28800)
        

class TestValueConversion(unittest.TestCase):

    def test_custom_data_value_converter(self):
        self.assertEquals(omniplan.CustomDataValueConverter.decode_omniplan_value([{'name': 'a', 'value': 'b'}]), {'a': 'b'})
        self.assertEquals(omniplan.CustomDataValueConverter.encode_omniplan_value({'a': 'b'}), [{'name': 'a', 'value': 'b'}])

    def test_fourcc_value_converter(self):
        self.assertEquals(omniplan.FourCharacterCodeValueConverter.decode_omniplan_value(1330664531), Task.TASK_TYPE_STANDARD)
        self.assertEquals(omniplan.FourCharacterCodeValueConverter.encode_omniplan_value(Task.TASK_TYPE_STANDARD), 1330664531)


class TestOmniPlanDocument(unittest.TestCase):

    def setUp(self):
        self.document = OmniPlanDocument('test.oplx')

    def test_first_open_document(self):
        self.assertEquals(OmniPlanDocument.first_open_document_name(), 'test.oplx')
        document = OmniPlanDocument.first_open_document()
        self.assertEquals(document.name, 'test.oplx')
        
    def test_document_name(self):
        self.assertEquals(self.document.name, 'test.oplx')

    def test_open_documents_names(self):
        self.assertEquals(OmniPlanDocument.all_open_documents_names(), ['test.oplx'])

    def test_dependencies(self):
        task = self.document.task_for_id(2)
        self.assertEquals(task.dependent_tasks()[0].name, 'Task 1')
        self.assertEquals(task.prerequisite_tasks()[0].name, 'Task 3')

    def test_value_conversion(self):
        task = self.document.task_for_id(2)
        self.assertEquals(task.effort, omniplan.WorkDayTimeInterval(workdays=1))
        
    def test_custom_value(self):
        task = self.document.tasks_for_custom_data_value('CustomKey', 'Custom Value 1')[0]
        self.assertEquals(task.name, 'Task 2')
        task = self.document.tasks_for_custom_data_value('CustomKey', 'Custom Value 2')[0]
        self.assertEquals(task.name, 'Task 4')
        tasks = self.document.tasks_for_custom_data_value('CustomKey', 'Custom Value 3')
        self.assertEquals(len(tasks), 2)

        self.assertIsNone(self.document.task_for_id(5).custom_data_value('DummyKey'))

    def test_change_task_value(self):
        task = self.document.task_for_id(2)
        self.assertEquals(task.effort, omniplan.WorkDayTimeInterval(workdays=1))
        task.effort = omniplan.WorkDayTimeInterval(workdays=0.5)
        self.assertEquals(task.effort, omniplan.WorkDayTimeInterval(workdays=0.5))
        self.assertEquals(task.change_records[0].property_name, 'effort')
        self.assertEquals(task.change_records[0].old_value, omniplan.WorkDayTimeInterval(workdays=1))
        task.commit_changes()
        task.effort = omniplan.WorkDayTimeInterval(workdays=1)
#         task.commit_changes(dry_run=True)
#         task.effort = omniplan.WorkDayTimeInterval(workdays=1)
        task.commit_changes()

        self.assertEquals(task.completed_effort, omniplan.WorkDayTimeInterval(workdays=0))
        task.completed_effort = omniplan.WorkDayTimeInterval(workdays=0.5)
        task.commit_changes()
        task.completed_effort = omniplan.WorkDayTimeInterval(workdays=0)
        task.commit_changes()

    def test_resource(self):
        resource = self.document.resource_for_id(1)
        self.assertEquals(resource.name, 'Resource 1')
        tasks = resource.assigned_tasks()
        self.assertEquals(len(tasks), 2)
        
        task = self.document.task_for_id(4)
        resources = task.assigned_resources()
        self.assertEquals(len(resources), 1)
        self.assertEquals(resources[0].name, 'Resource 1')
        
        resource = self.document.resource_for_name('Resource 1')
        self.assertEquals(resources[0], resource)

    def test_date(self):
        task = self.document.task_for_id(5)
        self.assertIsNotNone(task.starting_date.tzinfo)

    def test_create_task(self):
        task_name = b"\N{UMBRELLA} foo bar".decode('unicode-escape')
        task_data = {
            'effort': omniplan.WorkDayTimeInterval(workdays=1),
            'name': task_name,
        }
        task = self.document.create_task(task_data)
        self.assertIsNotNone(task)
        self.assertEquals(task.name, task_name)

        task.set_custom_data_value('CustomKey', 'Custom Value 4')
        self.assertEquals(task.custom_data_value('CustomKey'), 'Custom Value 4')
        lookup_tasks = self.document.tasks_for_custom_data_value('CustomKey', 'Custom Value 4')
        self.assertTrue(task in lookup_tasks)
        self.assertEquals(task.name, task_name)
        task.commit_changes()

        subtask_data = {
            'effort': omniplan.WorkDayTimeInterval(workdays=1),
            'name': "this is a subtask",
        }
        subtask = task.create_task(subtask_data)

    def test_assignment(self):
        task_name = b"\N{UMBRELLA} foo bar".decode('unicode-escape')
        task_data = {
            'effort': omniplan.WorkDayTimeInterval(workdays=1),
            'name': task_name,
        }
        task = self.document.create_task(task_data)
        
        resource = self.document.resource_for_name('Resource 2')
        if not resource:
            resource = self.document.create_resource(name='Resource 2')
        
        self.assertIsNotNone(resource)
        task.assign_to_resource(resource)
        task.commit_changes()

    def test_color(self):
        task_name = b"\N{UMBRELLA} color test".decode('unicode-escape')
        task_data = {
            'effort': omniplan.WorkDayTimeInterval(workdays=1),
            'name': task_name,
        }
        task = self.document.create_task(task_data)
        task.set_color(omniplan.Color.purple)
        task.commit_changes()


#     def test_example(self):
#         document = self.document
#         for task in document.all_tasks():
#             print '{}: effort {}'.format(task.name, task.effort)

#     def test_plist_representation(self):
#         print self.document.plist_representation()


        
if __name__ == "__main__":
    unittest.main()
    