#!/usr/bin/env python
#
# Written by Marc Liyanage
#
# Maintained at http://github.com/liyanage/omniplan-python
#

"""
Introduction
============

This module extracts project and task data from the OS X project management application
"OmniPlan" using its AppleScript interface and makes that data accessible to Python code.

The module is intended to be used mostly for read-only purposes, but there is limited
support for writing simple changes to task properties back into the OmniPlan project
document.

Usage
=====

Here is a simple usage example::

    from omniplan import OmniPlanDocument

    document = OmniPlanDocument.first_open_document()
    
    # Iterate over all tasks and extract some information
    for task in document.all_tasks():
        print '{}: effort {}'.format(task.name, task.effort)

    # Access a task by its ID
    task = document.task_for_id(1234)
    
    # Print all tasks that depend on this task
    dependency_tasks = task.dependencies()

    # Print all tasks that this task depends on
    prerequisite_tasks = task.prerequisites()

    # Change some values and write the changes back to the OmniPlan document
    task.effort = WorkDayTimeInterval(days=4.0)
    task.completed_effort = WorkDayTimeInterval(days=1.0)
    task.commit_changes()

"""

# autopep8 -i --ignore E501 xxx

import subprocess
import plistlib
import pickle
import struct
import datetime
import sys

class FourCharacterCode(object):

    @staticmethod
    def value_to_string(value):
        return struct.pack('>i', value)

    @staticmethod
    def string_to_value(string):
        return struct.unpack('>i', string)[0]


class AppleScript(object):

    def __init__(self, script):
        self.script = script

    def run(self, *arguments):
        cmd = 'osascript -'.split() + [str(i) for i in arguments]
        popen = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.stdout, self.stderr = popen.communicate(input=self.script.encode('utf-8'))
        self.stdout = self.stdout.rstrip()

    def plist_result(self):
        if not self.stdout:
            raise Exception("AppleScript code did not produce any output, unable to parse as plist")
        return plistlib.readPlistFromString(self.stdout)


class WorkDayTimeInterval(object):

    SECONDS_PER_WORKDAY = 8 * 60 * 60

    def __init__(self, seconds=None, workdays=None):

        self._seconds = 0

        if seconds:
            self._seconds = seconds
        elif workdays:
            self._seconds = workdays * self.SECONDS_PER_WORKDAY

    def seconds(self):
        return self._seconds

    def days(self):
        return self._seconds / self.SECONDS_PER_WORKDAY

    def __eq__(self, other):
        return self._seconds == other._seconds

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return '<WorkDayTimeInterval {:.1f} days>'.format(self.days())


class TimeInterval(object):

    SECONDS_PER_DAY = 24 * 60 * 60

    def __init__(self, seconds=None, days=None):
        self.seconds = 0

        if seconds:
            self.seconds = seconds
        elif days:
            self.seconds = days * self.SECONDS_PER_DAY

    def seconds(self):
        return self.seconds

    def days(self):
        return self.seconds / self.SECONDS_PER_DAY

    def __repr__(self):
        return '<TimeInterval {:.1f} days>'.format(self.days())


class AbstractValueConverter(object):

    @classmethod
    def decode_omniplan_value(cls, value):
        return value

    @classmethod
    def encode_omniplan_value(cls, value):
        return value


class WorkDayTimeIntervalValueConverter(AbstractValueConverter):

    @classmethod
    def decode_omniplan_value(cls, seconds):
        return WorkDayTimeInterval(seconds=seconds)

    @classmethod
    def encode_omniplan_value(cls, work_day_time_interval):
        return work_day_time_interval.seconds()


class CustomDataValueConverter(AbstractValueConverter):

    @classmethod
    def decode_omniplan_value(cls, pairs):
        return {pair['name']: pair['value'] for pair in pairs}

    @classmethod
    def encode_omniplan_value(cls, data_dict):
        return [{'name': key, 'value': value} for key, value in data_dict.items()]


class FourCharacterCodeValueConverter(AbstractValueConverter):

    @classmethod
    def decode_omniplan_value(cls, value):
        return FourCharacterCode.value_to_string(value)

    @classmethod
    def encode_omniplan_value(cls, string):
        return FourCharacterCode.string_to_value(string)


class UTCDateValueConverter(AbstractValueConverter):

    # UTC time zone class reference implementation from the Python "datetime" module documentation
    class UTC(datetime.tzinfo):
    
        def utcoffset(self, dt):
            return datetime.timedelta(0)
    
        def tzname(self, dt):
            return "UTC"
    
        def dst(self, dt):
            return datetime.timedelta(0)
    
    utc = UTC()

    @classmethod
    def decode_omniplan_value(cls, value):
        if not value:
            return None
        return value.replace(tzinfo=cls.utc)


class TaskChangeRecord(object):
    
    def targets_document(self):
        return False


class SimplePropertyTaskChangeRecord(TaskChangeRecord):

    def __init__(self, task, property_name):
        self.property_name = property_name
        self.old_value = getattr(task, property_name, None)
        self.task = task

    def change_applescript_code(self):
        applescript_value = self.task.applescript_value_for_property(self.property_name)
        applescript_property_name = self.task.applescript_name_for_property(self.property_name)
        return """set {} to {}""".format(applescript_property_name, applescript_value)

    def __repr__(self):
        return u'<Property change for task {}: property "{}", old value "{}", current value "{}">'.format(self.task, self.property_name, self.old_value, getattr(self.task, self.property_name))


class AddResourceAssignmentTaskChangeRecord(TaskChangeRecord):
    
    def __init__(self, resource_assignment):
        self.resource_assignment = resource_assignment

    def change_applescript_code(self):
        return """assign resource {} to task {} units {}""".format(self.resource_assignment.resource.id, self.resource_assignment.task.id, self.resource_assignment.units)

    def targets_document(self):
        return True

    def __repr__(self):
        return u'<Add resource assignment for task {}: resource {}>'.format(self.resource_assignment.task, self.resource_assignment.resource)


class SetCustomDataValueTaskChangeRecord(TaskChangeRecord):
    
    def __init__(self, task, name, value):
        self.name = name
        self.value = value
        self.task = task

    def change_applescript_code(self):
        return """make custom data entry with properties {{name:"{}", value:"{}"}}""".format(self.name, self.value)

    def __repr__(self):
        return u'<Set custom data value change for task {}: name "{}", value "{}">'.format(self.task, self.name, self.value)
    

class TaskCollection(object):
    """An abstract base class for classes that are containers for sets of tasks in a project."""

    def __init__(self, parent=None):
        self.tasks = []
        self.parent = parent

    def add_task(self, task):
#        print task
        self.tasks.append(task)
        self.document().task_added(task)

    def root(self):
        if self.parent:
            return self.parent.root()
        return self

    def document(self):
        return self.root()

    def self_and_descendants(self):
        yield self
        for task in self.descendants():
            yield task

    def descendants(self):
        for child_task in self.tasks:
            for task in child_task.self_and_descendants():
                yield task

    def print_tree(self):
        for task in self.descendants():
            indent = '--' * task.level()
            print indent + str(task)

    def add_tasks_for_task_data_list(self, task_data_list):
        for task_data in task_data_list:
            task = Task(task_data, self)
            self.add_task(task)
    
    def applescript_target_wrapper(self):
        pass
    
    def create_task(self, properties):
        properties = self.encoded_properties(properties)
        applescript_property_pairs = []
        for key, applescript_property_name, applescript_value in properties:
            applescript_property_pairs.append(u'{}: {}'.format(applescript_property_name, applescript_value))
        applescript_properties_string = u'{{{items}}}'.format(items=', '.join(applescript_property_pairs))

        make_task_string = u"""
        set newTask to make new task with properties {}
        return id of newTask
        """.format(applescript_properties_string)
        create_task_code = self.applescript_target_wrapper().format(make_task_string)
        cmd = AppleScript(create_task_code)
        cmd.run()
        task_id = cmd.stdout
        
        read_task_code = self.document().omniplan_task_data_query_applescript_code()
        cmd = AppleScript(read_task_code)
        cmd.run(self.document().name, task_id)
        task_data = cmd.plist_result()
        task = Task(task_data, self)
        self.add_task(task)
        return task
            
    def encoded_properties(self, properties):
        encoded_properties = []
        for key, value in properties.items():
            applescript_property_name = Task.applescript_name_for_property(key)
            applescript_value = Task.applescript_value_for_property_and_value(key, value)
            encoded_properties.append((key, applescript_property_name, applescript_value))
        return encoded_properties

    def level(self):
        if not self.parent:
            return 0
        return self.parent.level() + 1


class Task(TaskCollection):

    TASK_TYPE_STANDARD = 'OPTS'
    TASK_TYPE_MILESTOME = 'OPTM'
    TASK_TYPE_GROUP = 'OPTG'
    TASK_TYPE_HAMMOCK = 'OPTH'

    TASK_STATUS_CLOSE_TO_DUE_DATE = 'OPTc'
    TASK_STATUS_DUE_NOW = 'OPTd'
    TASK_STATUS_FINISHED = 'OPTm'
    TASK_STATUS_OK = 'OPTo'
    TASK_STATUS_PAST_DUE = 'OPTp'

    simple_properties = set('completed_effort ending_constraint_date outline_number ending_date duration remaining_effort effort id name total_cost priority starting_date starting_constraint_date prerequisites_data custom_data task_type task_status'.split())
    mutable_simple_properties = {
        'effort': {'quoted': False},
        'name': {'quoted': True},
        'completed_effort': {'quoted': False, 'applescript_property_name': 'completed effort'},
    }

    property_value_converter_map = {
        'effort': WorkDayTimeIntervalValueConverter,
        'completed_effort': WorkDayTimeIntervalValueConverter,
        'custom_data': CustomDataValueConverter,
        'task_type': FourCharacterCodeValueConverter,
        'task_status': FourCharacterCodeValueConverter,
        'ending_date': UTCDateValueConverter,
        'starting_constraint_date': UTCDateValueConverter,
        'starting_date': UTCDateValueConverter,
    }

    def __init__(self, task_data, parent=None):
        super(Task, self).__init__(parent)

        self.resource_assignments = []
        self.prerequisites = []
        self.dependents = []

        for key, value in task_data.items():

            converter_class = self.value_converter_for_property(key)
            if converter_class:
                try:
                    value = converter_class.decode_omniplan_value(value)
                except:
                    print >> sys.stderr, 'Unable to decode value of type {} for key "{}":'.format(type(value), key)
                    print value
                    raise

            if key in self.simple_properties:
                setattr(self, key, value)
                continue

            if key == 'child_tasks':
                self.add_tasks_for_task_data_list(value)
                continue

            raise Exception('Unknown key/value pair in task data: {0}'.format(key))

        actual_keys = set(task_data.keys())

        missing_simple_properties = self.simple_properties - actual_keys
        if missing_simple_properties:
            raise Exception('Missing key/value pair(s) in task data: {0}'.format(missing_simple_properties))

        # start capturing property updates
        self.change_records = []

    def custom_data_value(self, key):
        if key in self.custom_data:
            return self.custom_data[key]
        return None

    def __setattr__(self, key, value):
        if key in self.mutable_simple_properties:
            self.add_change_record(SimplePropertyTaskChangeRecord(self, key))
        super(Task, self).__setattr__(key, value)

    def set_custom_data_value(self, name, value):
        self.custom_data[name] = value
        self.document().update_custom_data_value_to_task_map_for_task(self)
        self.add_change_record(SetCustomDataValueTaskChangeRecord(self, name, value))

    def add_change_record(self, record):
        if not hasattr(self, 'change_records'):
            return

        self.change_records.append(record)

    def clear_change_records(self):
        del(self.change_records[:])

    @classmethod
    def value_converter_for_property(cls, property_name):
        return cls.property_value_converter_map.get(property_name)

    def converted_value_for_property(self, property_name):
        return self.converted_value_for_property_and_value(property_name, getattr(self, property_name))

    def applescript_value_for_property(self, property_name):
        return self.applescript_value_for_property_and_value(property_name, getattr(self, property_name))

    @classmethod
    def converted_value_for_property_and_value(cls, property_name, value):
        value_converter = cls.value_converter_for_property(property_name)
        if value_converter:
            value = value_converter.encode_omniplan_value(value)
        return value

    @classmethod
    def applescript_value_for_property_and_value(cls, property_name, value):
        value_description = cls.mutable_simple_properties.get(property_name)
        value = cls.converted_value_for_property_and_value(property_name, value)
        if value_description.get('quoted', False):
            value = value.replace('"', r'\"')
            return u'"{}"'.format(value)
        return value

    @classmethod
    def applescript_name_for_property(cls, property_name):
        value_description = cls.mutable_simple_properties.get(property_name)
        return value_description.get('applescript_property_name', property_name)

    def applescript_target_wrapper(self):
        task_target_code = u"""
            tell task {}
                {{}}
            end tell
        """.format(self.id)
        return self.document().applescript_target_wrapper().format(task_target_code)

    #### Resources

    def _add_resource_assignment(self, assignment):
        self.resource_assignments.append(assignment)

    def assigned_resources(self):
        return [assignment.resource for assignment in self.resource_assignments]

    def assign_to_resource(self, resource):
        assignment = ResourceAssignment(resource, self)
        self.add_change_record(AddResourceAssignmentTaskChangeRecord(assignment))

    #### Dependencies

    def add_dependent(self, dependency):
        self.dependents.append(dependency)

    def add_prerequisite(self, dependency):
        self.prerequisites.append(dependency)

    def dependent_tasks(self):
        return [dependency.dependent_task for dependency in self.dependents]

    def prerequisite_tasks(self):
        return [dependency.prerequisite_task for dependency in self.prerequisites]

    def has_dependents(self):
        return bool(self.dependents)

    def has_prerequisites(self):
        return bool(self.prerequisites)

    def has_dependencies(self):
        return self.has_dependents() or self.has_prerequisites()

    def commit_changes(self, dry_run=False):
        change_records = self.change_records
        
        records_targeting_document = [record for record in change_records if record.targets_document()]
        records_targeting_task = [record for record in change_records if not record.targets_document()]

        self.clear_change_records()
        
        change_applescript_code = '\n'.join(change_record.change_applescript_code() for change_record in records_targeting_task)
        change_applescript_code = self.applescript_target_wrapper().format(change_applescript_code)
        
        doc_change_applescript_code = '\n'.join(change_record.change_applescript_code() for change_record in records_targeting_document)
        doc_change_applescript_code = self.document().applescript_target_wrapper().format(doc_change_applescript_code)
        
        change_applescript_code += doc_change_applescript_code
        
#        print change_applescript_code

        if dry_run:
            print change_applescript_code
        else:
            cmd = AppleScript(change_applescript_code)
            cmd.run()

    #### Utilities

    def __repr__(self):
        return u'<Task {0}: {1}>'.format(self.id, self.name)


class TaskDependency(object):

    def __init__(self, prerequisite_task, dependent_task, dependency_type):
        self.prerequisite_task = prerequisite_task
        self.dependent_task = dependent_task
        self.dependency_type = dependency_type

        prerequisite_task.add_dependent(self)
        dependent_task.add_prerequisite(self)


class Resource(object):

    def __init__(self, resource_data):
        self.resource_assignments = []
        self.id = resource_data['id']
        self.name = resource_data['name']

    def _add_resource_assignment(self, assignment):
        self.resource_assignments.append(assignment)

    def assigned_tasks(self):
        return [assignment.task for assignment in self.resource_assignments]

    def __repr__(self):
        return u'<Resource {0} {1}>'.format(self.id, self.name)


class ResourceAssignment(object):

    def __init__(self, resource, task, units=1):
        self.resource = resource
        self.task = task
        self.units = units

        resource._add_resource_assignment(self)
        task._add_resource_assignment(self)

    def __repr__(self):
        return u'<ResourceAssignment resource={0} unit={1} task={2}>'.format(self.resource, self.units, self.task)


class OmniPlanDocument(TaskCollection):

    def __init__(self, name, allow_cache=False):
        super(OmniPlanDocument, self).__init__()
        self.name = name
        self.document_data_raw = None
        self.document_data = None
        self.selected_tasks = []
        self.selected_resources = []

        self.custom_data_value_to_task_map = {}
        self.task_map = {}
        self.resource_map = {}

        self.read_document(allow_cache=allow_cache)
        self.parse_document_data()

    def __repr__(self):
        return u'<OmniPlanDocument {0}>'.format(self.name)

    def read_document(self, allow_cache=False):
        script_code = self.omniplan_document_data_query_applescript_code()

        if allow_cache:
            try:
                with open('/tmp/omniplan-cache.dat') as f:
                    data = pickle.load(f)
                    if data:
                        self.document_data, self.document_data_raw = data
            except:
                pass

        if not self.document_data:
            cmd = AppleScript(script_code)
            cmd.run(self.name)
            if not cmd.stdout:
                raise Exception('Unable to get project data for OmniPlan document "{}", make sure that it is already open in OmniPlan'.format(self.name))
            self.document_data_raw = cmd.stdout
            self.document_data = cmd.plist_result()
            if allow_cache:
                with open('/tmp/omniplan-cache.dat', 'w') as f:
                    pickle.dump([self.document_data, self.document_data_raw], f)

    def plist_representation(self):
        return self.document_data_raw

    def applescript_target_wrapper(self):
        return u"""
        tell document "{}" of application "OmniPlan"
            {{}}
        end tell
        """.format(self.name)

    def parse_document_data(self):
        self.add_tasks_for_task_data_list(self.document_data['child_tasks'])
        self.parse_resources()
        self.process_dependencies()
        self.parse_selection()

    def process_dependencies(self):
        for task in self.all_tasks():
            for dependency_data_item in task.prerequisites_data:
                prerequisite_task = self.task_for_id(dependency_data_item['prerequisite_task_id'])
                dependent_task = self.task_for_id(dependency_data_item['dependent_task_id'])
                dependency_type = FourCharacterCode.value_to_string(dependency_data_item['dependency_type'])
                dependency = TaskDependency(prerequisite_task, dependent_task, dependency_type)

    def parse_resources(self):
        for resource_data in self.document_data['resources']:
            resource = Resource(resource_data)
            self.add_resource(resource)
            for assignment_data in resource_data['task_assignments']:
                task = self.task_for_id(assignment_data['task_id'])
                assignment = ResourceAssignment(resource, task, assignment_data['units'])
                #print assignment

    def parse_selection(self):
        self.selected_tasks.extend(self.task_for_id(id) for id in self.document_data['selected_task_ids'])
        self.selected_resources.extend(self.resource_for_id(id) for id in self.document_data['selected_resource_ids'])

    def add_resource(self, resource):
        self.resource_map[resource.id] = resource

    def task_added(self, task):
        self.task_map[task.id] = task
        self.update_custom_data_value_to_task_map_for_task(task)
    
    def update_custom_data_value_to_task_map_for_task(self, task):
        for key, value in task.custom_data.items():
            tasks = self.custom_data_value_to_task_map.setdefault(key, {}).setdefault(value, [])
            if task not in tasks:
                tasks.append(task)

    def tasks_for_custom_data_value(self, key, value):
        return self.custom_data_value_to_task_map.get(key, {}).get(value, [])

    def task_for_id(self, id):
        return self.task_map[id]

    def resource_for_id(self, id):
        return self.resource_map[id]

    def resource_for_name(self, name):
        for resource in self.resource_map.values():
            if resource.name == name:
                return resource
        return None

    def create_resource(self, name):
        make_resource_string = u"""
        set newResource to make new resource with properties {{name: "{}"}}
        return id of newResource
        """.format(name)
        
        create_resource_code = self.applescript_target_wrapper().format(make_resource_string)
        cmd = AppleScript(create_resource_code)
        cmd.run()
        resource_id = cmd.stdout
        
        resource = Resource({"id": resource_id, "name": name})
        self.add_resource(resource)
        return resource

    def all_tasks(self):
        return self.descendants()

    @classmethod
    def first_open_document(cls):
        return cls(cls.first_open_document_name())

    @classmethod
    def first_open_document_name(cls):
        document_name = cls.xth_open_document_name(1)

        if not document_name:
            raise Exception('Unable to get name of frontmost document')

        return document_name

    @classmethod
    def xth_open_document_name(cls, n):
        script_code = """
        tell application "OmniPlan"
            try
                return name of document of window {0}
            on error
                try
                    return name of document {0}
                on error
                    return ""
                end try
            end try
        end tell
        """.format(n)
        cmd = AppleScript(script_code)
        cmd.run()
        document_name = cmd.stdout

        if not document_name:
            return ""

        return document_name

    @classmethod
    def all_open_documents_names(cls):
        n = 1
        documents = []

        while True:
            document_name = cls.xth_open_document_name(n)

            if not document_name:
                break

            documents.append(document_name)
            n += 1

        return documents

    @classmethod
    def omniplan_task_data_query_applescript_code(cls):
        task_query_code = """
        on run argv
            set document_name to item 1 of argv
            set task_id to item 2 of argv as number

            tell application "OmniPlan"
                try
                    set |document| to document document_name
                on error
                    return ""
                end try

                set |task| to task task_id of |document|
            end tell
            set task_record to record_for_task(|task|)

            tell application "System Events"
                set task_plist_item to make new property list item with properties {kind:record, value:task_record}
            end tell

            return text of task_plist_item
        end run

        """
        return task_query_code + cls.omniplan_applescript_utils_code()

    @classmethod
    def omniplan_document_data_query_applescript_code(cls):
        doc_query_code = """
        on run argv
            set document_name to item 1 of argv

            (*
            tell application "OmniPlan"
                set document_name to name of document of window 1
            end tell
            *)

            tell application "OmniPlan"
                try
                    set |document| to document document_name
                on error
                    return ""
                end try

                set task_list to my child_task_list_for_parent(|document|)
                set resource_list to my resource_list_for_document(|document|)
                set selection_data to my get_selection_for_document(|document|)
                set document_data to {child_tasks:task_list, |resources|:resource_list} & selection_data
            end tell

            tell application "System Events"
                set root_plist_item to make new property list item with properties {kind:record, value:document_data}
            end tell

            return text of root_plist_item
        end run
        """
        
        return doc_query_code + cls.omniplan_applescript_utils_code()

    @classmethod
    def omniplan_applescript_utils_code(cls):
        return """
        on get_selection_for_document(|document|)
            set should_hide to false
            tell application "System Events"
                if visible of process "OmniPlan" is false then
                    set visible of process "OmniPlan" to true
                    set should_hide to true
                end if
            end tell

            set target_window to my window_for_document(|document|)
            set selected_task_ids to my selected_task_ids_for_window(target_window)
            set selected_resource_ids to my selected_resource_ids_for_window(target_window)
            if should_hide then
                tell application "System Events"
                    set visible of process "OmniPlan" to false
                end tell
            end if
            return {selected_task_ids:selected_task_ids, selected_resource_ids:selected_resource_ids}
        end get_selection_for_document

        on selected_task_ids_for_window(|window|)
            set selected_task_ids to {}
            tell application "OmniPlan"
                repeat with |task| in (selected tasks of |window| as list)
                    set end of selected_task_ids to id of |task|
                end repeat
            end tell
            return selected_task_ids
        end selected_task_ids_for_window

        on selected_resource_ids_for_window(|window|)
            set selected_resource_ids to {}
            tell application "OmniPlan"
                repeat with |resource| in (selected resources of |window| as list)
                    set end of selected_resource_ids to id of |resource|
                end repeat
            end tell
            return selected_resource_ids
        end selected_resource_ids_for_window

        on window_for_document(|document|)
            tell application "OmniPlan"
                repeat with |window| in windows
                    if document of |window| = |document| then
                        return |window|
                    end if
                end repeat
            end tell
            return missing value
        end window_for_document

        on record_for_task(|task|)
            using terms from application "OmniPlan"
                tell |task|
                    set custom_data to my custom_data_for_task(|task|)
                    set child_task_list to my child_task_list_for_parent(it)
                    set prerequisites_list to my prerequisites_list_for_task(it)
                    set task_record to {|id|:id, |name|:name, completed_effort:completed effort, |duration|:duration, |effort|:effort, ending_date:ending date, ending_constraint_date:my replace_missing_value(ending constraint date), outline_number:outline number, |priority|:priority, remaining_effort:remaining effort, starting_constraint_date:my replace_missing_value(starting constraint date), starting_date:starting date, task_status:task status, task_type:task type, total_cost:total cost, child_tasks:child_task_list, custom_data:custom_data, prerequisites_data:prerequisites_list}
                    return task_record
                end tell
            end using terms from
        end record_for_task

        on child_task_list_for_parent(parent)
            using terms from application "OmniPlan"
                set task_list to {}
                tell parent
                    repeat with child_task in child tasks
                        set end of task_list to my record_for_task(child_task)
                    end repeat
                end tell
                return task_list
            end using terms from
        end child_task_list_for_parent

        on prerequisites_list_for_task(task)
            using terms from application "OmniPlan"
                set prerequisites_list to {}
                repeat with |dependency| in prerequisites of |task|
                    tell |dependency|
                        set end of prerequisites_list to {dependency_type:dependency type, dependent_task_id:id of dependent task, prerequisite_task_id:id of prerequisite task, lead_percentage:lead percentage, lead_time:lead time}
                    end tell
                end repeat
                return prerequisites_list
            end using terms from
        end prerequisites_list_for_task

        on custom_data_for_task(task)
            using terms from application "OmniPlan"
                set custom_data to {}
                repeat with entry in custom data entries of |task|
                    set end of custom_data to {|name|:name of entry, |value|:my replace_missing_value(value of entry)}
                end repeat
                return custom_data
            end using terms from
        end custom_data_for_task

        on resource_list_for_document(|document|)
            using terms from application "OmniPlan"
                set resource_list to {}
                repeat with |resource| in resources of |document|
                    set assignment_list to my assignment_list_for_resource(|resource|)
                    tell |resource|
                        set end of resource_list to {|id|:id, |name|:name, task_assignments:assignment_list}
                    end tell
                end repeat
                return resource_list
            end using terms from
        end resource_list_for_document

        on assignment_list_for_resource(resource)
            using terms from application "OmniPlan"
                set assignment_list to {}
                repeat with |assignment| in assignments of |resource|
                    tell |assignment|
                        set end of assignment_list to {task_id:id of task of it, |units|:units}
                    end tell
                end repeat
                return assignment_list
            end using terms from
        end assignment_list_for_resource

        on replace_missing_value(value)
            if value is missing value then
                return ""
            end if
            return value
        end replace_missing_value
        """


if __name__ == "__main__":
    doc = OmniPlanDocument(OmniPlanDocument.first_open_document_name())
