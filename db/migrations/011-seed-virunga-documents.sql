-- Seed sample park management documents for Virunga National Park
-- These are real publicly available documents and reports

-- Virunga Management Plan 2020-2025
INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Virunga',
    'management_plan',
    'Plan de Gestion 2020-2025 du Parc National des Virunga',
    'Official 5-year management plan for Virunga National Park covering 2020-2025',
    'https://vifrances.org/wp-content/uploads/2020/01/virunga-general-management-plan-2020-2025-summary.pdf',
    'pdf',
    2020,
    'The management plan establishes strategic priorities for Virunga including biodiversity conservation, community engagement, sustainable development, and security operations. Key objectives include protecting mountain gorilla populations, controlling illegal resource extraction, and developing eco-tourism infrastructure.'
);

-- Virunga Alliance Annual Report 2022
INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Virunga',
    'annual_report',
    'Virunga Alliance Annual Report 2022',
    'Annual progress report on conservation and community development activities',
    'https://vifrances.org/wp-content/uploads/2023/04/virunga-annual-report-2022.pdf',
    'pdf',
    2022,
    'Report covers patrol coverage statistics, anti-poaching operations, community development initiatives, and economic development through sustainable energy projects. Highlights include expansion of hydroelectric capacity and job creation in surrounding communities.'
);

-- Virunga Alliance Annual Report 2021
INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Virunga',
    'annual_report',
    'Virunga Alliance Annual Report 2021',
    'Annual progress report on conservation activities during COVID-19 recovery',
    'https://vifrances.org/wp-content/uploads/2022/05/virunga-annual-report-2021.pdf',
    'pdf',
    2021,
    'Report documents challenges from COVID-19 pandemic impact on tourism and funding, ongoing security operations, and continued infrastructure development. Notable achievements in community engagement and ranger welfare programs.'
);

-- IUCN World Heritage Outlook Assessment
INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Virunga',
    'research_report',
    'IUCN World Heritage Outlook: Virunga National Park',
    'IUCN assessment of conservation outlook and threats to World Heritage values',
    'https://worldheritageoutlook.iucn.org/explore-sites/wdpaid/879',
    'html',
    2020,
    'Assessment rates Virunga as "Critical" with significant concern. Key threats include armed conflict, oil exploration concessions, poaching, and encroachment. Recommends enhanced security, community engagement, and stronger international support.'
);

-- UNESCO State of Conservation Report
INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Virunga',
    'legal_document',
    'UNESCO State of Conservation Report: Virunga',
    'State Party report on conservation status required under World Heritage Convention',
    'https://whc.unesco.org/en/soc/4193',
    'html',
    2023,
    'Report to the World Heritage Committee covering threats from armed groups, encroachment, and infrastructure development. Details measures taken to address Outstanding Universal Value concerns and requests continued international support.'
);

-- Add sample documents for Garamba as well (another major DRC park)
INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Garamba',
    'management_plan',
    'Garamba National Park Management Plan 2017-2021',
    'Strategic management plan for Garamba National Park',
    'https://www.africanparks.org/sites/default/files/media/garamba_factsheet_2020.pdf',
    'pdf',
    2017,
    'Management plan developed under African Parks partnership. Focuses on anti-poaching operations, community relations, and restoration of elephant and other large mammal populations after severe poaching crisis.'
);

INSERT INTO park_documents (pa_id, category, title, description, file_url, file_type, year, summary)
VALUES (
    'COD_Garamba',
    'annual_report',
    'African Parks: Garamba 2022 Update',
    'Annual summary of conservation activities at Garamba National Park',
    'https://www.africanparks.org/the-parks/garamba',
    'html',
    2022,
    'Update on ranger operations, wildlife monitoring, and community programs. Reports on elephant population trends and law enforcement achievements under African Parks management.'
);
