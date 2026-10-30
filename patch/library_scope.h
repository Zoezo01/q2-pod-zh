#ifndef Q2_LIBRARY_SCOPE_H
#define Q2_LIBRARY_SCOPE_H

/* A SQL placeholder, expanded only by the shared library query wrappers. */
#define LIBRARY_SCOPE_TOKEN "/*q2-library-scope*/1"
char *library_scope_query(const char *sql); /* caller frees; NULL on allocation failure */
int library_scope_hidden(const char *path);

#endif
