# IfcTester - IDS based model auditing
# Copyright (C) 2021 Artur Tomczak <artomczak@gmail.com>, Thomas Krijnen <mail@thomaskrijnen.com>, Dion Moult <dion@thinkmoult.com>
#
# This file is part of IfcTester.
#
# IfcTester is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcTester is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcTester.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

import builtins
import re
from functools import lru_cache
from logging import Logger
from typing import TYPE_CHECKING, Any, Literal, Optional, TypedDict, Union

import ifcopenshell.util.classification
import ifcopenshell.util.element
import ifcopenshell.util.unit
from xmlschema.validators import identities

if TYPE_CHECKING:
    from .ids import Specification


def cast_to_value(from_value, to_value):
    try:
        target_type = type(to_value).__name__
        if target_type == "int":
            # Casting str -> float means that notation like '1e3' is preserved
            # We do not cast to int because 42.0 == 42 and 42.3 != 42
            return float(from_value)
        elif target_type == "bool":
            if from_value in ("true", "1"):
                return True
            elif from_value in ("false", "0"):
                return False
        return builtins.__dict__[target_type](from_value)
    except ValueError:
        pass


# See bug 4716.
def is_x(value, cast_value):
    if cast_value >= 0:
        if value < cast_value * (1.0 - 1e-6) or value > cast_value * (1.0 + 1e-6):
            return False
    elif value > cast_value * (1.0 - 1e-6) or value < cast_value * (1.0 + 1e-6):
        return False
    return True


@lru_cache
def get_pset(element, pset):
    return ifcopenshell.util.element.get_pset(element, pset)


@lru_cache
def get_psets(element):
    return ifcopenshell.util.element.get_psets(element)


Cardinality = Literal["required", "optional", "prohibited"]


class FacetFailure(TypedDict):
    element: ifcopenshell.entity_instance
    reason: str


class Facet:
    cardinality: Cardinality

    def __init__(self, *parameters):
        self.status = None
        self.passed_entities: set[ifcopenshell.entity_instance] = set()
        self.failures: list[FacetFailure] = []
        for i, name in enumerate(self.parameters):
            setattr(self, name.replace("@", ""), parameters[i])

    def asdict(self, clause_type: str) -> dict[str, Any]:
        results = {}
        for name in self.parameters:
            value = getattr(self, name.replace("@", ""))
            if value is not None:
                if name == "@dataType":
                    value = value.upper()
                results[name] = value if "@" in name else self.to_ids_value(value)
        if clause_type == "applicability":
            for key in ["@uri", "@instructions", "@cardinality"]:
                results.pop(key, None)
        return results

    def parse(self, xml):
        setattr(self, "cardinality", "required")
        for name, value in xml.items():
            name = name.replace("@", "")
            if isinstance(value, dict) and "simpleValue" in value.keys():
                setattr(self, name, value["simpleValue"])
            elif isinstance(value, dict) and "restriction" in value.keys():
                setattr(self, name, Restriction().parse(value["restriction"]))
            else:
                setattr(self, name, value)
        return self

    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]]
    ) -> list[ifcopenshell.entity_instance]:
        if not elements:
            return []
        return [e for e in elements if self(e)]

    def to_string(
        self,
        clause_type: str,
        specification: Optional[Specification] = None,
        requirement: Optional[Facet] = None,
    ) -> str:
        if clause_type == "applicability":
            templates = self.applicability_templates
            if specification and specification.maxOccurs == 0:
                templates = self.prohibited_templates
        elif clause_type == "requirement":
            if specification and specification.maxOccurs == 0:
                return "The requirement is not applicable"
            if (not requirement) or isinstance(requirement, Entity) or requirement.cardinality == "required":
                templates = self.requirement_templates
            elif requirement.cardinality == "prohibited":
                templates = self.prohibited_templates
            elif requirement.cardinality == "optional":
                templates = self.requirement_templates
                templates = [
                    t.replace("shall", "may").replace("Shall", "May").replace("must", "may") for t in templates
                ]

        for template in templates:
            total_variables = len(template) - len(template.replace("{", ""))
            total_replacements = 0
            for key in self.parameters:
                key = key.replace("@", "")
                value = getattr(self, key)
                key_variable = "{" + key + "}"
                if value is not None and key_variable in template:
                    template = template.replace(key_variable, str(value))
                    total_replacements += 1
                if total_replacements == total_variables:
                    return template
        return "This facet cannot be interpreted"

    def to_ids_value(self, parameter: Union[str, Restriction, list]) -> dict[str, Any]:
        if isinstance(parameter, (int, float, str)):
            parameter_dict = {"simpleValue": str(parameter)}
        elif isinstance(parameter, Restriction):
            parameter_dict = {"xs:restriction": [parameter.asdict()]}
        elif isinstance(parameter, list):
            restrictions = {"@base": "xs:" + parameter[0].base}
            for p in parameter:
                x = p.asdict()
                restrictions[list(x)[1]] = x[list(x)[1]]
            parameter_dict = {"xs:restriction": [restrictions]}
        else:
            raise Exception(str(parameter) + " was not able to be converted into 'Parameter_dict'")
        return parameter_dict

    def get_usage(self) -> Cardinality:
        return self.cardinality

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> Result:
        raise NotImplementedError

    """ Extension Patrick Loibl: The following method has been added to check the attributes of a linked element.
    If the check is successful up to the point where the method is called and the facet has another property facet, the check begins.
    :param self: An instance of the PartOf class
    :type self: An instance of the PartOf class
    :param element: The IfcObject required for the property check
    :type element: IfcObject
    :param is_pass: Returns the status of the check
    :type is_pass: Boolean
    :param reason: Returns the reason if the check fails
    :type reason: None or Dictionary
    :returns: the variables is_pass and reason
    :rtype: a tuple
    """  
    def property_check(self, element, is_pass, reason):
        if is_pass and hasattr(self,"property") and self.property !=[] and hasattr(self,"extendedSpecification"):
            for facet in self.extendedSpecification:
                if isinstance(facet,Property):
                    result = facet(element)
                    is_pass = result.is_pass
                    reason = {"type": "ENTITY", "actual": "the entity has one or more false properties"}  
                    if not is_pass:
                        facet.failures.append(FacetFailure(element=element, reason=str(result)))
                        break 
                    else: facet.passed_entities.add(element)
            return is_pass,reason
        else:
            return is_pass,reason

    """ Extension Patrick Loibl: The following method has been added to check the attributes of a linked element.
    If the check is successful up to the point where the method is called and the facet has an attribute facet, the check begins. 
    :param self: Instance of the PartOf class
    :type self: Instance of the PartOf class
    :param element: The IfcObject required for the attribute check
    :type element: IfcObject
    :param is_pass: returns the status of the check
    :type is_pass: Boolean
    :param reason: returns the reason if the check fails
    :type reason: None or Dictionary
    :returns: the variables is_pass and reason
    :rtype: a tuple
    """
    def attribute_check(self, element,is_pass,reason):
        if is_pass and hasattr(self,"attribute") and self.attribute !=[] and hasattr(self,"extendedSpecification"):
            for facet in self.extendedSpecification:
                if isinstance(facet,Attribute):
                    result = facet(element)
                    is_pass = result.is_pass
                    reason = {"type": "ENTITY", "actual": "the entity has one or more false attributes"}    
                    if not is_pass:
                        facet.failures.append(FacetFailure(element=element, reason=str(result)))
                        break
                    else: facet.passed_entities.add(element)
            return is_pass,reason
        else:
            return is_pass,reason
        
    """ Extension Patrick Loibl: The following method has been added to check the attributes of a linked element.
    If the check is successful up to the point where the method is called and the facet has another material facet, the check begins.
    :param self: An instance of the PartOf class
    :type self: An instance of the PartOf class
    :param element: The IfcObject required for the property check
    :type element: IfcObject
    :param is_pass: Returns the status of the check
    :type is_pass: Boolean
    :param reason: Returns the reason if the check fails
    :type reason: None or Dictionary
    :returns: the variables is_pass and reason
    :rtype: a tuple
    """  
    def material_check(self, element,is_pass,reason):
        if is_pass and hasattr(self,"material") and self.material !=[] and hasattr(self,"extendedSpecification"):
            for facet in self.extendedSpecification:
                if isinstance(facet,Material):
                    result = facet(element)
                    is_pass = result.is_pass
                    if not is_pass:
                        reason = {"type": "ENTITY", "actual": "the entity has no or a false material"}
                        facet.failures.append(FacetFailure(element=element, reason=str(result)))
                        break
                    else: facet.passed_entities.add(element)    
            return is_pass,reason
        else:
            return is_pass,reason 

    """ Extension Patrick Loibl: The following method has been added to check the attributes of a linked element.
    If the check is successful up to the point where the method is called and the facet has another entity facet, the check begins.
    :param self: An instance of the PartOf class
    :type self: An instance of the PartOf class
    :param element: The IfcObject required for the property check
    :type element: IfcObject
    :param is_pass: Returns the status of the check
    :type is_pass: Boolean
    :param reason: Returns the reason if the check fails
    :type reason: None or Dictionary
    :returns: the variables is_pass and reason
    :rtype: a tuple
    """  
    def entity_check(self, element, is_pass, reason):
        if is_pass and hasattr(self,"entity") and self.entity !=[] and hasattr(self,"extendedSpecification"):
            for facet in self.extendedSpecification:
                if isinstance(facet,Entity):
                    result = facet(element)
                    is_pass = result.is_pass
                    if not is_pass:
                        reason = {"type": "ENTITY", "actual": "The element does not have the correct entity."}  
                        facet.failures.append(FacetFailure(element=element, reason=str(result)))
                        break
                    else: facet.passed_entities.add(element)
            return is_pass,reason
        else:
            return is_pass,reason

class Entity(Facet):
    def __init__(self, name="IFCWALL", predefinedType=None, instructions=None):
        self.parameters = ["name", "predefinedType", "@instructions"]
        self.applicability_templates = [
            "All {name} data of type {predefinedType}",
            "All {name} data",
        ]
        self.requirement_templates = [
            "Shall be {name} data of type {predefinedType}",
            "Shall be {name} data",
        ]
        self.prohibited_templates = [
            "Shall not be {name} data of type {predefinedType}",
            "Shall not be {name} data",
        ]
        super().__init__(name, predefinedType, instructions)

    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]] = None
    ) -> list[ifcopenshell.entity_instance]:
        if isinstance(elements, list):
            return super().filter(ifc_file, elements)

        if isinstance(self.name, str):
            try:
                results = ifc_file.by_type(self.name, include_subtypes=False)
            except:
                # If the user has specified a class that doesn't exist in the version
                results = []
                if not self.name.endswith("TYPE"):
                    try:
                        for element_type in ifc_file.by_type(f"{self.name}Type"):
                            results.extend(ifcopenshell.util.element.get_types(element_type))
                    except:
                        pass
        else:
            results = []
            ifc_classes = [t for t in ifc_file.wrapped_data.types() if t.upper() == self.name]
            for ifc_class in ifc_classes:
                try:
                    results.extend(ifc_file.by_type(ifc_class, include_subtypes=False))
                except:
                    # If the user has specified a class that doesn't exist in the version
                    continue
        if self.predefinedType:
            return [r for r in results if self(r)]
        return results

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> EntityResult:
        is_pass = inst.is_a().upper() == self.name
        reason = None

        if (
            not is_pass
            and inst.file.schema == "IFC2X3"
            and not self.name.endswith("TYPE")
            and (element_type := ifcopenshell.util.element.get_type(inst))
        ):
            is_pass = element_type.is_a().upper() == f"{self.name}TYPE"
            reason = {"type": "NAME", "actual": element_type.is_a().upper()[:-4]}
        elif not is_pass:
            reason = {"type": "NAME", "actual": inst.is_a().upper()}

        if is_pass and self.predefinedType:
            if self.predefinedType == "USERDEFINED":
                is_pass = ifcopenshell.util.element.is_userdefined_type(inst)
                if not is_pass:
                    predefined_type = ifcopenshell.util.element.get_predefined_type(inst)
            else:
                predefined_type = ifcopenshell.util.element.get_predefined_type(inst)
                is_pass = predefined_type == self.predefinedType

            if not is_pass:
                reason = {"type": "PREDEFINEDTYPE", "actual": predefined_type}

        # Extension Patrick Loibl
        if is_pass:
            self.passed_entities.add(inst)
        # Extension end   
        return EntityResult(is_pass, reason)


class Attribute(Facet):
    def __init__(self, name="Name", value=None, cardinality: Cardinality = "required", instructions=None): 
        self.parameters = ["name", "value", "@cardinality", "@instructions"]
        self.applicability_templates = [
            "Data where the {name} is {value}",
            "Data where the {name} is provided",
        ]
        self.requirement_templates = [
            "The {name} shall be {value}",
            "The {name} shall be provided",
        ]
        self.prohibited_templates = [
            "The {name} shall not be {value}",
            "The {name} shall not be provided",
        ]
        super().__init__(name, value, cardinality, instructions)
    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]]
    ) -> list[ifcopenshell.entity_instance]:
        if isinstance(elements, list):
            return super().filter(ifc_file, elements)

        results = []
        schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(ifc_file.schema_identifier)
        entities = {entity.name(): entity for entity in schema.entities()}

        def ignore_subtypes(entity):
            for subentity in entity.subtypes():
                # entity might be already removed as .entities() order is not hierarchical
                if entities.pop(subentity.name(), None):
                    ignore_subtypes(subentity)

        while entities:
            entity_name, entity = entities.popitem()
            for attribute in entity.attributes():
                if attribute.name() == self.name:
                    results.extend(ifc_file.by_type(entity_name, include_subtypes=True))
                    # e.g. if IfcRoot already has .Name, it's safe not to check all it's subtypes attributes
                    ignore_subtypes(entity)

        # TODO: perhaps we should consider value in the filter

        return results

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> AttributeResult:
        if isinstance(self.name, str):
            names = [self.name]
            attribute_type = inst.wrapped_data.get_attribute_category(self.name)
            if attribute_type == 1:  # Forward attribute
                values = [getattr(inst, self.name, None)]
            # Extension Patrick Loibl
            elif attribute_type ==2:
                # Here, the inverse attributes are automatically retrieved by a corresponding function in IfcTester
                values = [getattr(inst, self.name, None)]
            # Extension end
            else:
                values = []
        else:
            info = inst.get_info()
            names = []
            values = []
            for k, v in info.items():
                if k == self.name:
                    attribute_type = inst.wrapped_data.get_attribute_category(k)
                    if attribute_type == 1:  # Forward attribute
                        names.append(k)
                        values.append(v)

        is_pass = bool(values)
        reason = None

        if not is_pass:
            if self.cardinality == "optional":
                return AttributeResult(True)
            reason = {"type": "NOVALUE"}

        if is_pass:
            non_empty_values = []
            for i, value in enumerate(values):
                is_empty = False
                if value is None:
                    is_empty = True
                elif value == "":
                    is_empty = True
                elif value == tuple():
                    is_empty = True
                else:
                    argument_index = inst.wrapped_data.get_argument_index(names[i])
                    try:
                        attribute_type = inst.attribute_type(argument_index)
                        if attribute_type == "LOGICAL" and value == "UNKNOWN":
                            is_empty = True
                    except:
                        if names[i] in inst.wrapped_data.get_inverse_attribute_names():
                            #Extension Patrick Loibl: Changed to false to include inverse attributes
                            is_empty = False
                            #Extension end
                if not is_empty:
                    non_empty_values.append(value)
            if non_empty_values:
                values = non_empty_values
            else:
                is_pass = False
                reason = {"type": "FALSEY", "actual": values if len(values) > 1 else values[0]}

        # Part 1: normal value attributes are used
        if is_pass and self.value:
            #Extension Simon Fischer: Flatten the Return list for the case the Attribute value contains lists
            values = [x for item in values for x in (item if isinstance(item, tuple) else [item])]
            #Extension end
            
            for value in values:
                if isinstance(value, ifcopenshell.entity_instance):
                    is_pass = False
                    reason = {"type": "VALUE", "actual": value}
                    break
                elif isinstance(self.value, str) and isinstance(value, str):
                    if value != self.value:
                        is_pass = False
                        reason = {"type": "VALUE", "actual": value}
                        break
                elif isinstance(self.value, str):
                    cast_value = cast_to_value(self.value, value)
                    if isinstance(value, float) and isinstance(cast_value, float):
                        if not is_x(value, cast_value):
                            is_pass = False
                            reason = {"type": "VALUE", "actual": value}
                            break
                    elif value != cast_value:
                        is_pass = False
                        reason = {"type": "VALUE", "actual": value}
                        break
                elif value != self.value:
                    is_pass = False
                    reason = {"type": "VALUE", "actual": value}
                    break
            if is_pass:
               self.passed_entities.add(inst)     
        # Part 2: extended attribute facet
        # Extension Patrick Loibl: All the extensions of the attribute facet can be found in this elif-statement.
        elif is_pass and (hasattr(self,"entity") or hasattr(self,"attribute") or hasattr(self,"property") or hasattr(self,"material")):
            self.passed_entities.add(inst)
            #Extension Simon Fischer: Flatten the Return list for the case the Attribute value contains lists
            values = [x for item in values for x in (item if isinstance(item, tuple) else [item])]
            
            for value in values:
                is_pass = True            
                (is_pass,reason) = self.entity_check(value,is_pass,reason)
                (is_pass,reason) = self.attribute_check(value,is_pass,reason)
                (is_pass,reason) = self.property_check(value,is_pass,reason)
                (is_pass,reason) = self.material_check(value,is_pass,reason)

                if is_pass:
                    break
                else:
                    is_pass = False
                    reason = {"type": "VALUE", "actual": value}
        elif is_pass:
            self.passed_entities.add(inst)
        #Extension end

        if self.cardinality == "prohibited":
            return AttributeResult(not is_pass, {"type": "PROHIBITED"})
        return AttributeResult(is_pass, reason)
        
class Classification(Facet):
    def __init__(self, value=None, system=None, uri=None, cardinality: Cardinality = "required", instructions=None):
        self.parameters = ["value", "system", "@uri", "@cardinality", "@instructions"]
        self.applicability_templates = [
            "Data having a {system} reference of {value}",
            "Data classified using {system}",
            "Data classified as {value}",
        ]
        self.requirement_templates = [
            "Shall have a {system} reference of {value}",
            "Shall be classified using {system}",
            "Shall be classified as {value}",
        ]
        self.prohibited_templates = [
            "Shall not have a {system} reference of {value}",
            "Shall not be classified using {system}",
            "Shall not be classified as {value}",
        ]

        super().__init__(value, system, uri, cardinality, instructions)

    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]]
    ) -> list[ifcopenshell.entity_instance]:
        if isinstance(elements, list):
            return super().filter(ifc_file, elements)
        return ifc_file.by_type("IfcObjectDefinition")

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> ClassificationResult:
        leaf_references = ifcopenshell.util.classification.get_references(inst)

        references = leaf_references.copy()
        for leaf_reference in leaf_references:
            references.update(ifcopenshell.util.classification.get_inherited_references(leaf_reference))

        is_pass = bool(references)
        reason = None

        if not is_pass:
            if self.cardinality == "optional":
                return ClassificationResult(True)
            reason = {"type": "NOVALUE"}

        if is_pass and self.value:
            values = [getattr(r, "Identification", getattr(r, "ItemReference", None)) for r in references]
            is_pass = any([self.value == v for v in values])
            if not is_pass:
                reason = {"type": "VALUE", "actual": values}

        if is_pass:
            classifications = filter(None, (ifcopenshell.util.classification.get_classification(r) for r in references))
            systems = [r.Name for r in classifications]
            is_pass = any([self.system == s for s in systems])
            if not is_pass:
                reason = {"type": "SYSTEM", "actual": systems}

        if self.cardinality == "prohibited":
            return ClassificationResult(not is_pass, {"type": "PROHIBITED"})
        # Extension Patrick Loibl
        if is_pass:
            self.passed_entities.add(inst)
        # Extension end   
        return ClassificationResult(is_pass, reason)


class PartOf(Facet):
    def __init__(
        self,
        name="IFCWALL",
        predefinedType=None,
        relation=None,
        cardinality: Cardinality = "required",
        instructions=None,
    ):
        self.parameters = ["name", "predefinedType", "@relation", "@cardinality", "@instructions"]
        self.applicability_templates = [
            "An element with an {relation} relationship with an {name}",
            "An element with an {relation} relationship",
            "An element with a relationship with an {name}",
        ]
        self.requirement_templates = [
            "An element must have an {relation} relationship with an {name} of predefined type {predefinedType}",
            "An element must have an {relation} relationship with an {name}",
            "An element must have an {relation} relationship",
        ]
        self.prohibited_templates = [
            "An element must not have an {relation} relationship with an {name}",
            "An element must not have an {relation} relationship",
        ]
        super().__init__(name, predefinedType, relation, cardinality, instructions)

    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]]
    ) -> list[ifcopenshell.entity_instance]:
        if isinstance(elements, list):
            return super().filter(ifc_file, elements)
        return list(ifc_file)  # Lazy

    def asdict(self, clause_type: str) -> dict[str, Any]:
        results = super().asdict(clause_type)
        entity = {}
        if "name" in results:
            entity["name"] = results["name"]
            del results["name"]
        if "predefinedType" in results:
            entity["predefinedType"] = results["predefinedType"]
            del results["predefinedType"]
        if entity:
            results["entity"] = entity
        return results

    def parse(self, xml):
        if "entity" in xml:
            super().parse(xml["entity"])
        result = super().parse(xml)
        if hasattr(self, "entity"):
            delattr(self, "entity")
        return result

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> PartOfResult:
        reason = None
        if not self.relation:
            is_pass = False
            ancestors = []
            parent = self.get_parent(inst)
            while parent:
                ancestors.append(parent.is_a().upper())
                if parent.is_a().upper() == self.name:
                    if self.predefinedType:
                        predefined_type = ifcopenshell.util.element.get_predefined_type(parent)
                        ancestors[-1] += f".{predefined_type}"
                        if predefined_type == self.predefinedType:
                            is_pass = True
                    else:
                        is_pass = True
                    break
                parent = self.get_parent(parent)
            if not is_pass:
                reason = {"type": "ENTITY", "actual": ancestors}
            # Extension Patrick Loibl
            if is_pass:
                self.passed_entities.add(inst)
            # Extension end  
            (is_pass,reason) = self.attribute_check(parent,is_pass,reason) #  Extension Patrick Loibl
            (is_pass,reason) = self.property_check(parent,is_pass,reason) #  Extension Patrick Loibl
        elif self.relation == "IFCRELAGGREGATES":
            aggregate = ifcopenshell.util.element.get_aggregate(inst)
            is_pass = aggregate is not None
            if not is_pass:
                reason = {"type": "NOVALUE"}
            if is_pass and self.name:
                is_pass = False
                ancestors = []
                while aggregate is not None:
                    ancestors.append(aggregate.is_a().upper())
                    if aggregate.is_a().upper() == self.name:
                        if self.predefinedType:
                            predefined_type = ifcopenshell.util.element.get_predefined_type(aggregate)
                            ancestors[-1] += f".{predefined_type}"
                            if predefined_type == self.predefinedType:
                                is_pass = True
                        else:
                            is_pass = True
                        break
                    aggregate = ifcopenshell.util.element.get_aggregate(aggregate)
                if not is_pass:
                    reason = {"type": "ENTITY", "actual": ancestors}
            # Extension Patrick Loibl
            if is_pass:
                self.passed_entities.add(inst)
            # Extension end  
            (is_pass,reason) = self.attribute_check(aggregate,is_pass,reason) #  Extension Patrick Loibl
            (is_pass,reason) = self.property_check(aggregate,is_pass,reason) #  Extension Patrick Loibl
        elif self.relation == "IFCRELASSIGNSTOGROUP":
            group = None
            for rel in getattr(inst, "HasAssignments", []) or []:
                if rel.is_a("IfcRelAssignsToGroup"):
                    group = rel.RelatingGroup
                    break
            is_pass = group is not None
            if not is_pass:
                reason = {"type": "NOVALUE"}
            if is_pass and self.name:
                if group.is_a().upper() != self.name:
                    is_pass = False
                    reason = {"type": "ENTITY", "actual": group.is_a().upper()}
                if self.predefinedType:
                    predefined_type = ifcopenshell.util.element.get_predefined_type(group)
                    if predefined_type != self.predefinedType:
                        is_pass = False
                        reason = {"type": "PREDEFINEDTYPE", "actual": predefined_type}
            # Extension Patrick Loibl
            if is_pass:
                self.passed_entities.add(inst)
            # Extension end  
            (is_pass,reason) = self.attribute_check(group,is_pass,reason) #  Extension Patrick Loibl
            (is_pass,reason) = self.property_check(group,is_pass,reason) #  Extension Patrick Loibl
        elif self.relation == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            container = ifcopenshell.util.element.get_container(inst)
            is_pass = container is not None
            if not is_pass:
                reason = {"type": "NOVALUE"}
            if is_pass and self.name:
                if container.is_a().upper() != self.name:
                    is_pass = False
                    reason = {"type": "ENTITY", "actual": container.is_a().upper()}
                if is_pass and self.predefinedType:
                    predefined_type = ifcopenshell.util.element.get_predefined_type(container)
                    if predefined_type != self.predefinedType:
                        is_pass = False
                        reason = {"type": "PREDEFINEDTYPE", "actual": predefined_type}
            # Extension Patrick Loibl
            if is_pass:
                self.passed_entities.add(inst)
            # Extension end  
            (is_pass,reason) = self.attribute_check(container,is_pass,reason) #  Extension Patrick Loibl
            (is_pass,reason) = self.property_check(container,is_pass,reason) #  Extension Patrick Loibl
        elif self.relation == "IFCRELNESTS":
            nest = ifcopenshell.util.element.get_nest(inst)
            is_pass = nest is not None
            if not is_pass:
                reason = {"type": "NOVALUE"}
            if is_pass and self.name:
                is_pass = False
                ancestors = []
                while nest is not None:
                    ancestors.append(nest.is_a().upper())
                    if nest.is_a().upper() == self.name:
                        if self.predefinedType:
                            predefined_type = ifcopenshell.util.element.get_predefined_type(nest)
                            ancestors[-1] += f".{predefined_type}"
                            if predefined_type == self.predefinedType:
                                is_pass = True
                        else:
                            is_pass = True
                        break
                    nest = ifcopenshell.util.element.get_nest(nest)
                if not is_pass:
                    reason = {"type": "ENTITY", "actual": ancestors}
            # Extension Patrick Loibl
            if is_pass:
                self.passed_entities.add(inst)
            # Extension end  
            (is_pass,reason) = self.attribute_check(nest,is_pass,reason) #  Extension Patrick Loibl
            (is_pass,reason) = self.property_check(nest,is_pass,reason) #  Extension Patrick Loibl
        elif self.relation == "IFCRELVOIDSELEMENT IFCRELFILLSELEMENT":
            if inst.is_a("IfcOpeningElement"):
                building_element = ifcopenshell.util.element.get_voided_element(inst)
            else:
                building_element = None
                opening = ifcopenshell.util.element.get_filled_void(inst)
                if opening:
                    building_element = ifcopenshell.util.element.get_voided_element(opening)
            is_pass = building_element is not None
            if not is_pass:
                reason = {"type": "NOVALUE"}
            if is_pass and self.name:
                if building_element.is_a().upper() != self.name:
                    is_pass = False
                    reason = {"type": "ENTITY", "actual": building_element.is_a().upper()}
                if is_pass and self.predefinedType:
                    predefined_type = ifcopenshell.util.element.get_predefined_type(building_element)
                    if predefined_type != self.predefinedType:
                        is_pass = False
                        reason = {"type": "PREDEFINEDTYPE", "actual": predefined_type}
            # Extension Patrick Loibl
            if is_pass:
                self.passed_entities.add(inst)
            # Extension end  
            (is_pass,reason) = self.attribute_check(building_element,is_pass,reason) #  Extension Patrick Loibl 
            (is_pass,reason) = self.property_check(building_element,is_pass,reason) #  Extension Patrick Loibl
        #  Extension Patrick Loibl: This elif block was added to use the new relation.
        elif self.relation == "IFCRELSPACEBOUNDARY":
            type = inst.wrapped_data.is_a(True).split(".")[1]
            elements = []
            if type == "IfcSpace":
                # First, all spaces are retrieved and stored in a list.
                for i in range(0,len(inst.BoundedBy)): # If a relatedBuildingElement is “None”, it is not considered a SpaceBoundary and is therefore not added to the list
                    relatedBuildingElement = inst.BoundedBy[i].RelatedBuildingElement
                    if relatedBuildingElement!=None:
                        elements.append(relatedBuildingElement)
            elif hasattr(inst,"ProvidesBoundaries"):
                # First, all boundaries are retrieved and stored in a list.
                for i in range(0,len(inst.ProvidesBoundaries)):
                    elements.append(inst.ProvidesBoundaries[i].RelatingSpace)
            # If no boundaries are present, is_pass is set to False, a reason is created, and the check is complete.
            is_pass = bool(len(elements))
            if not is_pass:
                reason = {"type": "NOVALUE"}
            # If boundaries exist, the correct type is checked; depending on the result, `is_pass` is set accordingly, and the check continues.
            else:
                elements_to_check = set()
                for element in elements:
                    elementtyp = element.wrapped_data.is_a(True).split(".")[1]
                    if elementtyp.upper() == self.name:
                        elements_to_check.add(element)
                if len(elements_to_check) > 0:    
                    is_pass=True
                    # Extension Patrick Loibl
                    if is_pass:
                        self.passed_entities.add(inst)
                    # Extension end  
                    (is_pass,reason) = self.checkSpaceBoundary(elements_to_check, is_pass,reason)
                else:
                    is_pass = False
                    reason = {"type": "ENTITY", "actual": elementtyp}
        # Extension end

        if self.cardinality == "prohibited":
            return PartOfResult(not is_pass, {"type": "PROHIBITED"})
        return PartOfResult(is_pass, reason)

    """ Extension Patrick Loibl: The following method checks the attributes, properties, and other associated elements for the identified space boundaries.
    :param self: Instance of the PartOf class
    :type self: Instance of the PartOf class
    :param elements_to_check: Contains the spaces or boundary objects to be checked
    :type elements_to_check:  List of IFC objects
    :param is_pass: Returns the status of the check
    :type is_pass: Boolean
    :param reason: returns the reason if the check fails
    :type reason: None or Dictionary
    :returns: the variables is_pass and reason
    :rtype: a tuple
    """
    def checkSpaceBoundary(self, elements_to_check, is_pass, reason):
        # If at least one valid element is present, the attribute check is performed first.
        for element in elements_to_check:
            is_pass = True
            (is_pass,reason) = self.attribute_check(element,is_pass,reason)
            #if not is_pass:
            #    break # Eigentlich nicht unbedingt notwendig, aber beschleunigt den Code, da es sofort abbricht bei einem falschen Ergebnis.
            if is_pass:
                break
        if is_pass:
            # If no difference in properties is required or an incorrect keyword is specified, the first block runs.
            # This block performs the property check for the respective elements. As soon as a element meets the criterion, the check can be terminated.
            if (not hasattr(self,"multiElementProcessing")) or (hasattr(self,"multiElementProcessing") and self.multiElementProcessing not in ["allElementsMustComply", "noElementMayComply", "samePropertyValuesForAllElements", "differentPropertyValuesForAnyElement","differentElementsForEachProperty"]):
                for element in elements_to_check:
                    is_pass = True
                    (is_pass,reason) = self.property_check(element,is_pass,reason)
                    if is_pass:
                        break 
            elif hasattr(self,"multiElementProcessing") and self.multiElementProcessing == "allElementsMustComply":
                (is_pass,reason) =  self.property_check_combined(elements_to_check,is_pass,reason,"all")
            elif hasattr(self,"multiElementProcessing") and self.multiElementProcessing == "noElementMayComply":
                (is_pass,reason) =  self.property_check_combined(elements_to_check,is_pass,reason,"none")
            # This block covers the case where adjacent objects are supposed to have the same value for a property.
            # Since there must not be a predefined property value, a property check is not necessary.
            # A check to determine whether the property even exists is performed in propertyValueComparisonOfMultipleElements.
            elif hasattr(self,"multiElementProcessing") and self.multiElementProcessing == "samePropertyValuesForAllElements":
                (is_pass,reason)=self.propertyValueComparisonOfMultipleElements(elements_to_check, "same")          
            # The third block handles the case where different properties must be present for the respective rooms.
            # First, the system checks whether at least one room meets the property requirements.
            # Then, it checks for the different properties.
            elif hasattr(self,"multiElementProcessing") and self.multiElementProcessing == "differentPropertyValuesForAnyElement":
                for element in elements_to_check:    
                    is_pass=True
                    (is_pass,reason) = self.property_check(element,is_pass,reason)
                    if is_pass:
                        break                                
                if is_pass:
                    (is_pass,reason)=self.propertyValueComparisonOfMultipleElements(elements_to_check, "different")
            # The last block covers the case where at least one element must be present for each of the specified properties.
            # First, the properties to be checked are identified. Then, each element is checked until it satisfies a property.
            # That property is then removed from the requirements, and the process continues with the next element.
            # Once all properties have been satisfied, the test is passed.
            elif hasattr(self,"multiElementProcessing") and self.multiElementProcessing == "differentElementsForEachProperty" and hasattr(self,"extendedSpecification"):
                properties_to_check = [specification for specification in self.extendedSpecification if isinstance(specification, Property)]
                for element in elements_to_check:
                    for property_to_check in properties_to_check[:]:
                        if property_to_check(element).is_pass:
                            properties_to_check.remove(property_to_check)
                            break
                        else:
                            continue
                if len(properties_to_check) == 0:
                    is_pass = True
                else:
                    is_pass = False
                    reason = {"type": "ENTITY", "actual": "there are no different elements"}
        
        return is_pass,reason

    """ Extension Patrick Loibl: The following method detects a difference in the properties for the “differentPropertiesForAnyElement” type.
    The properties of the respective elements are retrieved and stored in a set. If more than one value is stored in the set, there is a difference.
    :param self: Instance of the PartOf class
    :type self: Instance of the PartOf class
    :param elements_to_check: Contains the rooms or boundary objects from which the properties are to be determined
    :type elements_to_check: List of IfcObjects
    :param value: Determines whether the properties must be either different or the same
    :type value: String
    :returns: the variables `is_pass` and `reason`
    :rtype: a tuple
    """
    def propertyValueComparisonOfMultipleElements(self, elements_to_check, value):
        is_pass=False
        elementsWithoutProperty = False
        if len(elements_to_check) <= 1:
            is_pass = False
            reason = {"type": "ENTITY", "actual": "not enough related entities found"}
        else:
            if hasattr(self,"extendedSpecification"):
                for facet in self.extendedSpecification:
                    if isinstance(facet,Property):
                        different_values = set()
                        for element in elements_to_check:
                            result_property = facet(element)
                            if result_property.reason["type"] == "VALUE":
                                element_property_value = result_property.reason["actual"]
                                different_values.add(element_property_value)
                            else:
                                elementsWithoutProperty = True
                        if value == "different":
                            if (len(different_values) > 1) or (len(different_values) >= 1 and elementsWithoutProperty):
                                is_pass = True
                                reason = None
                            else:
                                is_pass = False
                                reason = {"type": "ENTITY", "actual": "the entities have no difference between their properties"}
                                break
                        elif value == "same":
                            if len(different_values) == 1 and not elementsWithoutProperty:
                                is_pass = True
                                reason = None
                            else:
                                is_pass = False
                                reason = {"type": "ENTITY", "actual": "the entities have no same property"}
                                break 
        return is_pass,reason
    
    """ Extension Simon Fischer: Method to check Property facets for a group of elements combined.
    If the keyword is none, no element is allowed to fulfil the facet.
    If the keyword is all, all elements must fulfil the facet.
    :param self: Instance of the PartOf class
    :type self: Instance of the PartOf class
    :param elements_to_check: List of IFC objects to be checked
    :type element_toCheck: List
    :param is_pass: Returns the status of the check
    :type is_pass: Boolean
    :param reason: Returns the reason if the check fails
    :type reason: None or Dictionary
    :returns: The variables is_pass and reason
    :rtype: A tuple
    """
    def property_check_combined(self,elements_to_check,is_pass,reason,keyword):
        if is_pass and hasattr(self,"property") and self.property !=[] and hasattr(self,"extendedSpecification"):
            for facet in self.extendedSpecification:
                if isinstance(facet,Property):
                    number_of_passed_elements = 0
                    for element in elements_to_check:
                        result = facet(element)
                        element_is_pass = result.is_pass
                          
                        if element_is_pass:
                            number_of_passed_elements += 1
                    if (keyword == "none" and number_of_passed_elements > 0) or (keyword == "all" and number_of_passed_elements < len(elements_to_check)):
                        is_pass = False
                        reason = {"type": "ENTITY", "actual": "the entity has one or more false properties"}
                        break
            return is_pass,reason
        else:
            return is_pass,reason

    def get_parent(self, element):
        parent = ifcopenshell.util.element.get_parent(element)
        if not parent:
            for rel in getattr(element, "HasAssignments", []) or []:
                if rel.is_a("IfcRelAssignsToGroup"):
                    parent = rel.RelatingGroup
                    break
        return parent


class Property(Facet):
    def __init__(
        self,
        propertySet="Property_Set",
        baseName="PropertyName",
        value=None,
        dataType=None,
        uri=None,
        cardinality: Cardinality = "required",
        instructions=None,
    ):
        self.parameters = [
            "propertySet",
            "baseName",
            "value",
            "@dataType",
            "@uri",
            "@cardinality",
            "@instructions",
        ]
        self.applicability_templates = [
            "Elements with {baseName} data of {value} in the dataset {propertySet}",
            "Elements with {baseName} data in the dataset {propertySet}",
        ]
        self.requirement_templates = [
            "{baseName} data shall be {value} and in the dataset {propertySet}",
            "{baseName} data shall be provided in the dataset {propertySet}",
        ]
        self.prohibited_templates = [
            "{baseName} data shall not be {value} and in the dataset {propertySet}",
            "{baseName} data shall not be provided in the dataset {propertySet}",
        ]
        super().__init__(propertySet, baseName, value, dataType, uri, cardinality, instructions)

    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]]
    ) -> list[ifcopenshell.entity_instance]:
        if isinstance(elements, list):
            return super().filter(ifc_file, elements)
        if ifc_file.schema == "IFC2X3":
            return ifc_file.by_type("IfcObjectDefinition")
        return (
            ifc_file.by_type("IfcObjectDefinition")
            + ifc_file.by_type("IfcMaterialDefinition")
            + ifc_file.by_type("IfcProfileDef")
        )

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> PropertyResult:
        if isinstance(self.propertySet, str):
            pset = get_pset(inst, self.propertySet)
            psets = {self.propertySet: pset} if pset else {}
        else:
            all_psets = get_psets(inst)
            psets = {k: v for k, v in all_psets.items() if k == self.propertySet}

        is_pass = bool(psets)
        reason = None

        if not is_pass:
            if self.cardinality == "optional":
                return PropertyResult(True)
            reason = {"type": "NOPSET", "actual": self.propertySet} # Extension Patrick Loibl: The name of the PropertySet is specified

        if is_pass:
            props = {}
            for pset_name, pset_props in psets.items():
                props[pset_name] = {}
                if isinstance(self.baseName, str):
                    prop = pset_props.get(self.baseName)
                    if prop == "UNKNOWN" and next(
                        p
                        for p in self.get_properties(inst.wrapped_data.file.by_id(pset_props["id"]))
                        if p.Name == self.baseName
                    ).NominalValue.is_a("IfcLogical"):
                        pass
                    elif prop is not None and prop != "":
                        props[pset_name][self.baseName] = prop
                else:
                    props[pset_name] = {
                        k: v for k, v in pset_props.items() if k == self.baseName and v is not None and v != ""
                    }

                if not bool(props[pset_name]):
                    if self.cardinality == "optional":
                        return PropertyResult(True)
                    is_pass = False
                    reason = {"type": "NOVALUE"}
                    break

                pset_entity = inst.wrapped_data.file.by_id(pset_props["id"])

                is_property_supported_class = True
                for prop_entity in self.get_properties(pset_entity):
                    if prop_entity.Name not in props[pset_name].keys():
                        continue
                    if not isinstance(prop_entity, ifcopenshell.entity_instance):
                        # Predefined properties are special :(
                        pass
                    elif prop_entity.is_a("IfcPropertySingleValue"):
                        data_type = prop_entity.NominalValue.is_a()

                        if self.dataType and data_type.lower() != self.dataType.lower():
                            is_pass = False
                            reason = {"type": "DATATYPE", "actual": data_type, "dataType": self.dataType}
                            break

                        unit = ifcopenshell.util.unit.get_property_unit(prop_entity, inst.wrapped_data.file)
                        if unit and getattr(unit, "Name", None):
                            # TODO support unnamed derived units
                            output_prefix = "KILO" if unit.UnitType == "MASSUNIT" else None
                            props[pset_name][prop_entity.Name] = ifcopenshell.util.unit.convert(
                                prop_entity.NominalValue.wrappedValue,
                                getattr(unit, "Prefix", None),
                                unit.Name,
                                output_prefix,
                                ifcopenshell.util.unit.si_type_names[unit.UnitType],
                            )
                    elif prop_entity.is_a("IfcPhysicalSimpleQuantity"):
                        prop_schema = prop_entity.wrapped_data.declaration().as_entity()
                        data_type = prop_schema.attribute_by_index(3).type_of_attribute().declared_type().name()

                        if self.dataType and data_type.lower() != self.dataType.lower():
                            is_pass = False
                            reason = {"type": "DATATYPE", "actual": data_type, "dataType": self.dataType}
                            break

                        unit = ifcopenshell.util.unit.get_property_unit(prop_entity, inst.wrapped_data.file)
                        if unit:
                            props[pset_name][prop_entity.Name] = ifcopenshell.util.unit.convert(
                                prop_entity[3],
                                getattr(unit, "Prefix", None),
                                unit.Name,
                                None,
                                ifcopenshell.util.unit.si_type_names[unit.UnitType],
                            )
                    elif prop_entity.is_a("IfcPropertyEnumeratedValue"):
                        if not prop_entity.EnumerationValues:
                            is_pass = False
                            reason = {"type": "NOVALUE"}
                            break
                        data_type = prop_entity.EnumerationValues[0].is_a()
                        if self.dataType and data_type.lower() != self.dataType.lower():
                            is_pass = False
                            reason = {"type": "DATATYPE", "actual": data_type, "dataType": self.dataType}
                            break
                    elif prop_entity.is_a("IfcPropertyListValue"):
                        if not prop_entity.ListValues:
                            is_pass = False
                            reason = {"type": "NOVALUE"}
                            break
                        data_type = prop_entity.ListValues[0].is_a()
                        if self.dataType and data_type.lower() != self.dataType.lower():
                            is_pass = False
                            reason = {"type": "DATATYPE", "actual": data_type, "dataType": self.dataType}
                            break
                        unit = ifcopenshell.util.unit.get_property_unit(prop_entity, inst.wrapped_data.file)
                        if unit:
                            props[pset_name][prop_entity.Name] = [
                                ifcopenshell.util.unit.convert(
                                    v,
                                    getattr(unit, "Prefix", None),
                                    unit.Name,
                                    None,
                                    ifcopenshell.util.unit.si_type_names[unit.UnitType],
                                )
                                for v in props[pset_name][prop_entity.Name]
                            ]
                    elif prop_entity.is_a("IfcPropertyBoundedValue"):
                        values = []
                        for attribute in ["UpperBoundValue", "LowerBoundValue", "SetPointValue"]:
                            value = getattr(prop_entity, attribute)
                            if value is not None:
                                data_type = value.is_a()
                                values.append(value.wrappedValue)
                        if self.dataType and data_type.lower() != self.dataType.lower():
                            is_pass = False
                            reason = {"type": "DATATYPE", "actual": data_type, "dataType": self.dataType}
                            break
                        unit = ifcopenshell.util.unit.get_property_unit(prop_entity, inst.wrapped_data.file)
                        if unit:
                            values = [
                                ifcopenshell.util.unit.convert(
                                    v,
                                    getattr(unit, "Prefix", None),
                                    unit.Name,
                                    None,
                                    ifcopenshell.util.unit.si_type_names[unit.UnitType],
                                )
                                for v in values
                            ]
                        props[pset_name][prop_entity.Name] = values
                    elif prop_entity.is_a("IfcPropertyTableValue"):
                        values = []
                        units = ifcopenshell.util.unit.get_property_table_unit(prop_entity, inst.wrapped_data.file)
                        for attribute in ["Defining", "Defined"]:
                            column_values = props[pset_name][prop_entity.Name][f"{attribute}Values"]
                            if not column_values:
                                continue
                            data_type = column_values[0].is_a()
                            if self.dataType and data_type.lower() == self.dataType.lower():
                                column_values = [v.wrappedValue for v in column_values]
                                unit = units[f"{attribute}Unit"]
                                if unit:
                                    column_values = [
                                        ifcopenshell.util.unit.convert(
                                            v,
                                            getattr(unit, "Prefix", None),
                                            unit.Name,
                                            None,
                                            ifcopenshell.util.unit.si_type_names[unit.UnitType],
                                        )
                                        for v in column_values
                                    ]
                                values.extend(column_values)
                        if not values:
                            is_pass = False
                            reason = {"type": "DATATYPE", "actual": data_type, "dataType": self.dataType}
                            break
                        props[pset_name][prop_entity.Name] = values
                    else:
                        is_property_supported_class = False

                if not is_property_supported_class:
                    is_pass = False
                    reason = {"type": "NOVALUE"}

                if not is_pass:
                    break

                reason = {"type": "VALUE", "actual": props[pset_name][self.baseName]} # Extension Patrick Loibl: Always add the value to the reason if there is a value (even if is_pass = true)

                if self.value:
                    for value in props[pset_name].values():
                        if isinstance(self.value, str) and isinstance(value, str):
                            # "i_require_foo" = "i_have_bar"
                            if value != self.value:
                                is_pass = False
                                reason = {"type": "VALUE", "actual": value}
                                break
                        elif isinstance(self.value, str) and isinstance(value, list):
                            # "i_require_foo" = ["a", "b"] such as in enumerated properties
                            cast_value = cast_to_value(self.value, value[0])
                            if cast_value not in value:
                                is_pass = False
                                reason = {"type": "VALUE", "actual": value}
                                break
                        elif not isinstance(self.value, str) and isinstance(value, list):
                            # XSD restriction = ["a", "b"] such as in enumerated properties
                            does_any_pass = [v for v in value if v == self.value]
                            if not does_any_pass:
                                is_pass = False
                                reason = {"type": "VALUE", "actual": value}
                                break
                        elif isinstance(self.value, str):
                            # "42" = 42
                            cast_value = cast_to_value(self.value, value)
                            if isinstance(value, float) and isinstance(cast_value, float):
                                if not is_x(value, cast_value):
                                    is_pass = False
                                    reason = {"type": "VALUE", "actual": value}
                                    break
                            elif value != cast_value:
                                is_pass = False
                                reason = {"type": "VALUE", "actual": value}
                                break
                        elif value != self.value:
                            # XSD restriction = whatever
                            is_pass = False
                            reason = {"type": "VALUE", "actual": value}
                            break

        if self.cardinality == "prohibited":
            return PropertyResult(not is_pass, {"type": "PROHIBITED"})
        # Extension Patrick Loibl
        if is_pass:
            self.passed_entities.add(inst)
        # Extension end   
        return PropertyResult(is_pass, reason)

    def get_properties(self, pset):
        if pset.is_a("IfcPropertySet"):
            return pset.HasProperties
        elif pset.is_a("IfcElementQuantity"):
            return pset.Quantities
        elif pset.is_a("IfcMaterialProperties") or pset.is_a("IfcProfileProperties"):
            return pset.Properties
        elif pset.is_a("IfcPreDefinedPropertySet"):
            return [
                type("", (object,), {"Name": k, "Value": v})()
                for k, v in pset.get_info().items()
                if not isinstance(v, ifcopenshell.entity_instance)
            ]


class Material(Facet):
    def __init__(self, value=None, uri=None, cardinality: Cardinality = "required", instructions=None):
        self.parameters = ["value", "@uri", "@cardinality", "@instructions"]
        self.applicability_templates = [
            "All data with a {value} material",
            "All data with a material",
        ]
        self.requirement_templates = [
            "Shall have a material of {value}",
            "Shall have a material",
        ]
        self.prohibited_templates = [
            "Shall not have a material of {value}",
            "Shall not have a material",
        ]
        super().__init__(value, uri, cardinality, instructions)

    def filter(
        self, ifc_file: ifcopenshell.file, elements: Optional[list[ifcopenshell.entity_instance]]
    ) -> list[ifcopenshell.entity_instance]:
        if isinstance(elements, list):
            return super().filter(ifc_file, elements)
        return ifc_file.by_type("IfcObjectDefinition")

    def __call__(self, inst: ifcopenshell.entity_instance, logger: Optional[Logger] = None) -> MaterialResult:
        # Extension Patrick Loibl: The value parameter is used as the name, and the other facets are used as usual.
        if self.cardinality == "optional":
            return MaterialResult(True)
        
        material = ifcopenshell.util.element.get_material(inst, should_skip_usage=True) # The material associated with the entity is retrieved

        is_pass = material is not None
        reason = None

        if not is_pass: # If there is no material at all, that's the end of it here
            reason = {"type": "NOVALUE"}
        else:
            materialUsed = []
            materialUsed.append(material)
            if material.is_a("IfcMaterial"):
                pass  # Nothing to do here, since it's already on the list
            elif material.is_a("IfcMaterialList"):
                materialUsed.extend(material.Materials) # IfcMaterialList will be removed in a future major release of the IFC standard  
            elif material.is_a("IfcMaterialLayerSet"):
                materialUsed.extend(l for l in material.MaterialLayers)
                materialUsed.extend(l.Material for l in material.MaterialLayers)
            elif material.is_a("IfcMaterialProfileSet"):
                materialUsed.extend(p for p in material.MaterialProfiles)
                materialUsed.extend(p.Material for p in material.MaterialProfiles)
            elif material.is_a("IfcMaterialConstituentSet"):
                materialUsed.extend(c for c in material.MaterialConstituents)
                materialUsed.extend(c.Material for c in material.MaterialConstituents)

            materialUsed = [x for x in materialUsed if x is not None]
            wrongValues = []
            for materialIfc in materialUsed:
                name = getattr(materialIfc, "LayerSetName", None) if materialIfc.is_a() == "IfcMaterialLayerSet" else getattr(materialIfc, "Name", None)
                category = getattr(materialIfc, "Category", None)

                if (hasattr(self,"value") and name == self.value or category == self.value) or not hasattr(self,"value"):
                    is_pass = True
                    # Extension Patrick Loibl
                    if is_pass:
                        self.passed_entities.add(inst)
                    # Extension end  
                    (is_pass,reason) = self.entity_check(materialIfc,is_pass,reason)
                    (is_pass,reason) = self.attribute_check(materialIfc,is_pass,reason)
                    (is_pass,reason) = self.property_check(materialIfc,is_pass,reason)
                    if is_pass:
                        break # If a material meets all requirements, the inspection can be terminated.
                else:
                    is_pass = False
                    wrongValues.extend([name,category])
                    reason = {"type": "VALUE", "actual": wrongValues} if (reason == None or reason["type"] != "ENTITY")else reason
        # Extension end

        if self.cardinality == "prohibited":
            return MaterialResult(not is_pass, {"type": "PROHIBITED"})
        return MaterialResult(is_pass, reason)    

class Restriction:
    def __init__(self, options=None, base="string"):
        self.base = base
        self.options = options or {}

    def parse(self, ids_dict):
        if not ids_dict:
            return self
        self.base = ids_dict.get("@base", "xs:string")[3:]
        for key, value in ids_dict.items():
            key = key.split(":")[-1]
            if key in ["@base", "annotation"]:
                continue
            if key == "enumeration" and isinstance(value, dict):
                self.options[key] = [value["@value"]]  # A single enumeration value which is pretty meaningless
            elif isinstance(value, dict):
                self.options[key] = value["@value"]
            else:
                self.options[key] = [v["@value"] for v in value]
        return self

    def asdict(self) -> dict[str, Any]:
        result = {"@base": "xs:" + self.base}
        for constraint, value in self.options.items():
            value = [value] if not isinstance(value, list) else value
            for v in value:
                if constraint in [
                    "length",
                    "minLength",
                    "maxLength",
                    "maxExclusive",
                    "maxInclusive",
                    "minExclusive",
                    "minInclusive",
                    "totalDigits",
                    "fractionDigits",
                ]:
                    value_dict = {"@value": v}
                else:
                    value_dict = {"@value": str(v)}
                result.setdefault(f"xs:{constraint}", []).append(value_dict)
        return result

    def __eq__(self, other):
        if other is None:
            return False
        for constraint, value in self.options.items():
            try:
                if constraint == "enumeration":
                    if other not in [cast_to_value(v, other) for v in value]:
                        return False
                elif constraint == "pattern":
                    if not isinstance(other, str):
                        return False
                    value = value if isinstance(value, list) else [value]
                    for pattern in value:
                        if re.compile(identities.translate_pattern(pattern)).fullmatch(other) is None:
                            return False
                elif constraint == "length":
                    if len(str(other)) != int(value):
                        return False
                elif constraint == "maxLength":
                    if len(str(other)) > int(value):
                        return False
                elif constraint == "minLength":
                    if len(str(other)) < int(value):
                        return False
                elif constraint == "maxExclusive":
                    if float(other) >= float(value):
                        return False
                elif constraint == "maxInclusive":
                    if float(other) > float(value):
                        return False
                elif constraint == "minExclusive":
                    if float(other) <= float(value):
                        return False
                elif constraint == "minInclusive":
                    if float(other) < float(value):
                        return False
            except ValueError:
                return False
        return True

    def __str__(self):
        return str(self.options)


class Result:
    def __init__(self, is_pass: bool, reason: Optional[dict[str, Any]] = {"type": None}): # Extension Patrick Loibl: There is always a reason
        self.is_pass = is_pass
        self.reason = reason

    def __bool__(self):
        return self.is_pass

    def __str__(self):
        return "" if self.is_pass else self.to_string()

    def to_string(self):
        return str(self.reason) or "The requirements were not met for some inexplicable reason. Good luck!"


class EntityResult(Result):
    def to_string(self):
        if self.reason["type"] == "NAME":
            return f"The entity class \"{self.reason['actual']}\" does not meet the required IFC class"
        elif self.reason["type"] == "PREDEFINEDTYPE":
            return f"The predefined type \"{str(self.reason['actual'])}\" does not meet the required type"


class AttributeResult(Result):
    def to_string(self):
        if self.reason["type"] == "NOVALUE":
            return "The required attribute did not exist"
        elif self.reason["type"] == "FALSEY":
            return f"The attribute value \"{str(self.reason['actual'])}\" is empty"
        elif self.reason["type"] == "INVALID":
            return "An invalid attribute name was specified in the IDS"
        elif self.reason["type"] == "VALUE":
            return f"The attribute value \"{str(self.reason['actual'])}\" does not match the requirement"
        elif self.reason["type"] == "PROHIBITED":
            return "The attribute value should not have met the requirement"


class ClassificationResult(Result):
    def to_string(self):
        if self.reason["type"] == "NOVALUE":
            return "The entity has no classification"
        elif self.reason["type"] == "VALUE":
            return f"The references \"{str(self.reason['actual'])}\" do not match the requirements"
        elif self.reason["type"] == "SYSTEM":
            return f"The systems \"{str(self.reason['actual'])}\" do not match the requirements"
        elif self.reason["type"] == "PROHIBITED":
            return "The classification should not have met the requirement"


class PartOfResult(Result):
    def to_string(self):
        if self.reason["type"] == "NOVALUE":
            return "The entity has no relationship"
        elif self.reason["type"] == "ENTITY":
            return f"The entity has a relationship with incorrect entities: \"{str(self.reason['actual'])}\""
        elif self.reason["type"] == "PREDEFINEDTYPE":
            return f"The entity has a relationship with incorrect predefined type: \"{str(self.reason['actual'])}\""
        elif self.reason["type"] == "PROHIBITED":
            return "The relationship should not have met the requirement"


class PropertyResult(Result):
    def to_string(self):
        if self.reason["type"] == "NOPSET":
            return "The required property set does not exist"
        elif self.reason["type"] == "NOVALUE":
            return "The property set does not contain the required property"
        elif self.reason["type"] == "DATATYPE":
            return f"The property's data type \"{str(self.reason['actual'])}\" does not match the required data type of \"{str(self.reason['dataType'])}\""
        elif self.reason["type"] == "VALUE":
            if isinstance(self.reason["actual"], list):
                if len(self.reason["actual"]) == 1:
                    return f"The property value \"{str(self.reason['actual'][0])}\" does not match the requirements"
                else:
                    return f"The property values \"{str(self.reason['actual'])}\" do not match the requirements"
            else:
                return f"The property value \"{str(self.reason['actual'])}\" does not match the requirements"
        elif self.reason["type"] == "PROHIBITED":
            return f"The property should not have met the requirement"
        

class MaterialResult(Result):
    def to_string(self):
        if self.reason["type"] == "NOVALUE":
            return "The entity has no material"
        elif self.reason["type"] == "VALUE":
            return (
                f"The material names and categories of \"{str(self.reason['actual'])}\" does not match the requirement"
            )
        elif self.reason["type"] == "PROHIBITED":
            return f"The material should not have met the requirement"
        # Extension Patrick Loibl
        elif self.reason["type"] == "PROPERTY":
            return f"The material has a false property"
        elif self.reason["type"] == "ATTRIBUTE":
            return f"The material has a false attribute"
        elif self.reason["type"] == "ENTITY":
            return f"The material entity is incorrect: \"{str(self.reason['actual'])}\""
        # Extension end
