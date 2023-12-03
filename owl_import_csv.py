from typing import List, TextIO, Set
from dataclasses import dataclass, field
import csv
from argparse import ArgumentParser

import rdflib
from rdflib.compare import to_isomorphic, graph_diff
from fuzzywuzzy import process

PREFIX_MAP = {
    'owl': 'http://www.w3.org/2002/07/owl#',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
}
''' XML prefixes '''

def fix_cause(cause: str) -> str:
    ''' Correct typos in causes. '''
    cause = snake_case(cause)

    # Disabled to encourage correctness.
    # Feel free to turn this back on.
    # if cause == 'misjudgement_of_hazardous_situation':
    #     return 'misjudgment_of_hazardous_situation'

    # if cause == 'malfunction_in_security_or_warning':
    #     return 'malfunction_in_securing_or_warning'
    
    return cause

@dataclass
class AccidentCase:
    source: str
    title: str
    description: str
    date: str
    type: str
    degree: str
    case_number: str

    causes: List[str] =  field(default_factory=list)
    equipments : List[str] = field(default_factory=list)

    def uri(self) -> str:
        ''' URI that represents this specific accident case uniquely. '''
        return f'http://www.semanticweb.org/lanre/ontologies/sso#{self.case_number}'

    def normalized_degree(self) -> str:
        ''' Degree of injury, 
            normalized to 'fatal_injury' or 'non_fatal_injury'. '''

        if 'nonfatal' in self.degree or 'non-fatal' in self.degree:
            return 'non_fatal_injury'
        if 'fatal' in self.degree:
            return 'fatal_injury'
        raise ValueError("Could not find degree of injury")

    def fixed_causes(self) -> str:
        ''' The cause is sometimes mispelled.
            This function returns the cause with some mispellings resolved.
        '''

        return [
            fix_cause(cause)
            for cause in self.causes
        ]


def read_csv_cases(file:TextIO) -> List[AccidentCase]:
    ''' 
    Read accident cases to import from CSV.
    Non-trivial due to multiple rows corresponding to one case.
    '''
    reader = csv.DictReader(file)
    cases: List[AccidentCase] = []
    for row in reader:
        # If the 'Source' value is present,
        # then this is the first row of a new case.
        if row['Source']:
            cases.append(AccidentCase(
                source=row['Source'],
                type=row['Accident type'],
                title=row['Title'],
                description=row['Description'],
                # Date column name somtimes has a leading space.
                date=row.get('Date') or row.get(' Date'),
                degree=row['Degree of injury'],
                case_number=row['Case Number']
            ))

        cause = row['Accident cause'].strip()
        if cause:
            cases[-1].causes.append(cause)
        
        equipment = row['Equipment'].strip()
        if equipment:
            cases[-1].equipments.append(equipment)

    return cases

def read_existing_accident_cases(g: rdflib.Graph) -> Set[str]:
    ''' Read existing accident cases from RDF graph,
        so we can ensure we do not import data
        that is already present. '''
    return {
        str(result[0])
        for result in g.query('''
SELECT ?s WHERE { 
    ?s a <http://www.semanticweb.org/lanre/ontologies/sso#Accident_case> .
}         
        ''')
    }

def read_named_individual_uris(g: rdflib.Graph) -> Set[str]:
    return {
        str(result[0])
        for result in g.query('''
SELECT ?s WHERE {
    ?s a <http://www.w3.org/2002/07/owl#NamedIndividual> .
}
        ''')
    }

def snake_case(text: str) -> str:
    ''' Transform space-separated text into underscore-separated text. '''
    return '_'.join(text.split(' '))

def main():
    parser = ArgumentParser(
        prog="OwlImportCSV",
        description="Import CSV files to OWL file"
    )
    parser.add_argument('--owl', help='Base OWL file   (before import)', required=True)
    parser.add_argument('--csv', help='Data CSV file   (to import)', required=True)
    parser.add_argument('--out', help='Output OWL file (after import)', required=True)
    args = parser.parse_args()

    with open(args.csv) as data_file:
        import_cases = read_csv_cases(data_file)

    g  = rdflib.Graph()
    g.parse(args.owl)
    existing_cases = read_existing_accident_cases(g)

    named_individuals = read_named_individual_uris(g)

    def assert_uri_exists(uri: rdflib.URIRef) -> rdflib.URIRef:
        ''' Assert that a URI reference to a NamedIndividual actually exists.
            Used to catch fault types.
            If URI exists, acts as no-op/identity function. Otherwise, errors. '''
        if str(uri) not in named_individuals:
            guess,confidence = process.extractOne(str(uri), named_individuals)
            print(guess,confidence)
            raise ValueError(
                "owl:NamedIndividual with this URI does not exist: "+str(uri)+"\n"
                +f"Did you mean this?  ({confidence}%) --  {guess}"
            )

        return uri

    for case in import_cases:
        uri = f'http://www.semanticweb.org/lanre/ontologies/sso#{case.case_number}'
        #print(uri)
        if uri in existing_cases:
            raise ValueError(f"Already have case uri = {uri}")

        sso = rdflib.Namespace('http://www.semanticweb.org/lanre/ontologies/sso#')
        
        ref = rdflib.URIRef(uri)
        # Create initial* case node.
        # [*] Order doesn't actually matter.
        g.add((ref,rdflib.RDF.type,rdflib.OWL.NamedIndividual))
        g.add((ref,rdflib.RDF.type,sso['Accident_case']))
        # Add Title.
        g.add((
            ref,
            sso['Title'],
            rdflib.Literal(case.title, datatype=rdflib.XSD.string)
        ))

        # If a date is actually present:
        if case.date.strip() != '':
            # Add date.
            g.add((
                ref,
                sso['Date'],
                rdflib.Literal(case.date, datatype=rdflib.XSD.dateTime)
            ))
        # Add description.
        g.add((
            ref,
            sso['Description'],
            rdflib.Literal(case.description, datatype=rdflib.XSD.string)
        ))
        # Add source.
        g.add((
            ref,
            sso['Source'],
            rdflib.Literal(case.source, datatype=rdflib.XSD.anyURI)
        ))
        # Add accident type.
        g.add((
            ref,
            sso['has_accident_type'],
            assert_uri_exists(sso[snake_case(case.type)])
        ))
        # Add all accident causes.
        for cause in case.fixed_causes():
            g.add((
                ref,
                sso['has_accident_cause'],
                assert_uri_exists(sso[cause])
            ))
        # Add all involved equipment.
        for equipment in case.equipments:
            g.add((
                ref,
                sso['involves_the_use_of'],
                assert_uri_exists(sso[snake_case(equipment)])
            ))
        # Add result (degree of injury)
        g.add((
            ref,
            sso['resulted_in'],
            assert_uri_exists(sso[case.normalized_degree()])
        ))
        
    print("Import Complete")
    print(f'{args.owl} + {args.csv} => {args.out}')


if __name__ == '__main__':
    main()