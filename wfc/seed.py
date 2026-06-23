"""
Seed the database with 4 demo methods.

Module: demo_pipeline
Methods: preprocess, filter_cells, label, aggregate

Each method has one tracked function with a few param_defs
so we can show meaningful parameter tracking.
"""

from .database import get_session
from .models import Module, Method, TrackedFunction, ParamDef


def seed():
    """Insert demo module + 3 methods + tracked functions + param_defs."""
    with get_session() as session:
        # Check if already seeded
        from sqlmodel import select
        existing = session.exec(select(Module).where(Module.name == "demo_pipeline")).first()
        if existing:
            print("DB already seeded — skipping.")
            return

        # Module
        mod = Module(name="demo_pipeline", description="3-step demo pipeline")
        session.add(mod)
        session.commit()
        session.refresh(mod)

        # ----- Method 1: preprocess -----
        m1 = Method(
            module_id=mod.id,
            name="preprocess",
            script_path="methods/preprocess/preprocess.py",
        )
        session.add(m1)
        session.commit()
        session.refresh(m1)

        tf1 = TrackedFunction(method_id=m1.id, function_name="preprocess_data", ordinal=1)
        session.add(tf1)
        session.commit()
        session.refresh(tf1)

        for pname, ptype, default in [
            ("normalize", "bool", "true"),
            ("scale_factor", "float", "1.0"),
        ]:
            session.add(ParamDef(
                tracked_function_id=tf1.id,
                param_name=pname, param_type=ptype, default_value=default,
            ))

        # ----- Method 2: filter_cells -----
        m2 = Method(
            module_id=mod.id,
            name="filter_cells",
            script_path="methods/filter_cells/filter_cells.py",
        )
        session.add(m2)
        session.commit()
        session.refresh(m2)

        tf2 = TrackedFunction(method_id=m2.id, function_name="filter_data", ordinal=1)
        session.add(tf2)
        session.commit()
        session.refresh(tf2)

        for pname, ptype, default in [
            ("min_quality", "float", "0.5"),
            ("remove_outliers", "bool", "true"),
        ]:
            session.add(ParamDef(
                tracked_function_id=tf2.id,
                param_name=pname, param_type=ptype, default_value=default,
            ))

        # ----- Method 3: label -----
        m3 = Method(
            module_id=mod.id,
            name="label",
            script_path="methods/label/label.py",
        )
        session.add(m3)
        session.commit()
        session.refresh(m3)

        tf3 = TrackedFunction(method_id=m3.id, function_name="apply_labels", ordinal=1)
        session.add(tf3)
        session.commit()
        session.refresh(tf3)

        for pname, ptype, default in [
            ("threshold", "float", "0.5"),
            ("label_column", "str", "label"),
        ]:
            session.add(ParamDef(
                tracked_function_id=tf3.id,
                param_name=pname, param_type=ptype, default_value=default,
            ))

        # ----- Method 4: aggregate -----
        m4 = Method(
            module_id=mod.id,
            name="aggregate",
            script_path="methods/aggregate/aggregate.py",
        )
        session.add(m4)
        session.commit()
        session.refresh(m4)

        tf4 = TrackedFunction(method_id=m4.id, function_name="aggregate_stats", ordinal=1)
        session.add(tf4)
        session.commit()
        session.refresh(tf4)

        for pname, ptype, default in [
            ("group_by", "str", "sample"),
            ("agg_method", "str", "mean"),
        ]:
            session.add(ParamDef(
                tracked_function_id=tf4.id,
                param_name=pname, param_type=ptype, default_value=default,
            ))

        session.commit()
        print("Seeded: module 'demo_pipeline' with methods: preprocess, filter_cells, label, aggregate")


if __name__ == "__main__":
    seed()
