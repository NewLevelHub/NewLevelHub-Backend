"""
Board templates — predefined column structures for new CRM boards.

Each template entry maps a string ID to:
  name_key    — i18n key for the template's display name
  column_keys — ordered list of i18n keys for the column names

The DEFAULT_TEMPLATE_ID ('basic') reproduces the legacy three-column layout so
existing callers that omit template_id get exactly the same behaviour as before.
"""

BOARD_TEMPLATES = {
    'basic': {
        'name_key': 'crm.template.basic',
        'column_keys': [
            'crm.default_column_todo',
            'crm.default_column_in_progress',
            'crm.default_column_done',
        ],
    },
    'sales': {
        'name_key': 'crm.template.sales',
        'column_keys': [
            'crm.template.sales.col_1',
            'crm.template.sales.col_2',
            'crm.template.sales.col_3',
            'crm.template.sales.col_4',
        ],
    },
    'recruitment': {
        'name_key': 'crm.template.recruitment',
        'column_keys': [
            'crm.template.recruitment.col_1',
            'crm.template.recruitment.col_2',
            'crm.template.recruitment.col_3',
            'crm.template.recruitment.col_4',
            'crm.template.recruitment.col_5',
        ],
    },
    'project': {
        'name_key': 'crm.template.project',
        'column_keys': [
            'crm.template.project.col_1',
            'crm.template.project.col_2',
            'crm.template.project.col_3',
            'crm.template.project.col_4',
        ],
    },
}

DEFAULT_TEMPLATE_ID = 'basic'
