#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

将movie中的actor_ids和director_ids转存到person_obj表中

"""

try:
    from . import database as db
except ImportError:
    import database as db

def collect_people(rows):
    """Extract unique person IDs from the movie credits stored in MySQL."""
    people = {}
    for row in rows:
        for credits in (row.get("actor_ids"), row.get("director_ids")):
            for value in (credits or "").split("|"):
                if ":" not in value:
                    continue
                name, person_id = value.split(":", 1)
                person_id = person_id.strip()
                if person_id:
                    people[person_id] = name.replace('"', "").strip()
    return list(people.items())


def main():
    with db.connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT actor_ids, director_ids FROM movies")
        people = collect_people(cursor.fetchall())
        cursor.executemany(
            "INSERT INTO person_obj (person_id, name) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            people,
        )
    db.connection.commit()
    print("person_obj synchronized: %s records" % len(people))


if __name__ == "__main__":
    main()
